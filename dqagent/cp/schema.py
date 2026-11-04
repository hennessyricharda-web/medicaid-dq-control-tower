"""Control-plane Postgres schema (DESIGN v2.1 §2.5, §3.5, §7).

The DDL is GENERATED here so that the lifecycle transition table has one source,
`dqagent.model.lifecycle`. Applying it twice is safe: every create is idempotent,
except the role bootstrap, which runs once per cluster.

Security properties enforced in the database (not only in Python):
  * dq_gov owns everything. Service roles have no CREATE on any schema.
  * Append-only tables: UPDATE, DELETE and TRUNCATE are revoked, and BEFORE
    UPDATE/DELETE/TRUNCATE triggers raise.
  * Unit heads change ONLY through the role-bound wrappers dq_ctl.transition_<role>()
    (SECURITY DEFINER, pinned search_path), generated from lifecycle.VIA_ROLE. Each is
    granted ONLY to its role and accepts only that role's verbs; the core
    dq_ctl._transition() is executable by nobody. It checks the allowed-transition table
    and the expected state_version (CAS), and appends the event in the same transaction.
    Units can only be INSERTed as LANDED/version 0, and a fingerprint is write-once.
  * EXECUTE is revoked from PUBLIC by default.
  * Row-level security is FORCE'd on scoped tables. The scope comes from
    dq_ctl.set_scope(), called only by the api inside a transaction; an unscoped
    session sees zero rows.
"""
from __future__ import annotations

import contextlib

from dqagent.model.lifecycle import HUMAN_VIAS, TERMINAL, VIA_ROLE, sql_allowed_transitions_values, wrapper_for

SERVICE_ROLES = ["dq_api", "dq_eval_svc", "dq_seal_svc", "dq_publish_svc", "dq_verify_svc", "dq_auditor",
                 "dq_triage_svc", "dq_registry_svc", "dq_monitor_svc",
                 "dq_trace_svc",        # Part F P1-1: the only writer of trace rows (ADJ-7)
                 "dq_discovery_svc"]    # Part F P1-3 (K2): writes dq_ctl.gate_checks / gate_check_transitions

APPEND_ONLY = [
    "manifest_reservations", "measure_results", "unit_events", "decisions", "decision_tokens", "files", "quarantine_proposals", "attestations", "roster", "deny", "quarantine",
    "audit_log", "results", "abc", "loads_events", "manifest_versions", "registry_events",
]


def roles_ddl() -> str:
    stmts = []
    for r in SERVICE_ROLES:
        stmts.append(
            f"DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{r}') "
            f"THEN CREATE ROLE {r} NOLOGIN; END IF; END$$;"
        )
        # T1: each service connects AS its own role (local trust auth), so no service session can
        # RESET ROLE into another identity (SEC-W1-2). Production: workload identity per service.
        stmts.append(f"ALTER ROLE {r} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;")
    return "\n".join(stmts)


def transition_wrappers_ddl() -> str:
    """One SECURITY DEFINER wrapper per role, generated from lifecycle.VIA_ROLE (single source)."""
    by_fn: dict[str, list[str]] = {}
    for via in VIA_ROLE:
        by_fn.setdefault(wrapper_for(via.value), []).append(via.value)
    role_of = {wrapper_for(v.value): r for v, r in VIA_ROLE.items()}
    human = ",".join(f"'{v.value}'" for v in sorted(HUMAN_VIAS, key=lambda x: x.value))
    out = []
    for fn, vias in sorted(by_fn.items()):
        role = role_of[fn]
        vlist = ",".join(f"'{v}'" for v in sorted(vias))
        actor = "p_actor" if role == "dq_api" else f"'{role}'"
        out.append(f"""
CREATE OR REPLACE FUNCTION dq_ctl.{fn}(
  p_unit text, p_expected_version int, p_to text, p_via text, p_actor text, p_payload jsonb)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF p_via NOT IN ({vlist}) THEN
    RAISE EXCEPTION 'role {role} may not drive via %', p_via USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- Part E: vetoing a unit inside its veto window is recorded by dq_ctl.veto_unit (person, reason, window by the
  -- database clock); the plain wrapper cannot take that edge
  IF p_via = 'VETO' AND (SELECT u.state FROM dq_ctl.units u WHERE u.unit_key = p_unit) = 'EVALUATING' THEN
    RAISE EXCEPTION 'a veto inside the window goes through dq_ctl.veto_unit' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_via IN ({human}) AND p_to = 'PENDING_RELEASE' AND NOT EXISTS (
       SELECT 1 FROM dq_ctl.decisions d WHERE d.unit_key = p_unit AND d.round = p_expected_version
         AND d.person_id = p_actor) THEN
    RAISE EXCEPTION 'no decision by % in round % for %', p_actor, p_expected_version, p_unit
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN dq_ctl._transition(p_unit, p_expected_version, p_to, p_via, {actor}, p_payload);
END $$;
REVOKE ALL ON FUNCTION dq_ctl.{fn}(text,int,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.{fn}(text,int,text,text,text,jsonb) TO {role};""")
    return "\n".join(out)


def schema_ddl() -> str:
    terminal = ",".join(f"'{s.value}'" for s in sorted(TERMINAL, key=lambda x: x.value))
    append_only_triggers = "\n".join(
        f"""
DROP TRIGGER IF EXISTS {t}_no_mut ON dq_ctl.{t};
CREATE TRIGGER {t}_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.{t}
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS {t}_no_trunc ON dq_ctl.{t};
CREATE TRIGGER {t}_no_trunc BEFORE TRUNCATE ON dq_ctl.{t}
  FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON dq_ctl.{t} FROM PUBLIC, {", ".join(SERVICE_ROLES)};"""
        for t in APPEND_ONLY
    )
    decision_rls = "\n".join(f"""
ALTER TABLE dq_ctl.{t} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS {t}_scope ON dq_ctl.{t};
CREATE POLICY {t}_scope ON dq_ctl.{t} FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.unit_key = {t}.unit_key));
DROP POLICY IF EXISTS {t}_ins ON dq_ctl.{t};
CREATE POLICY {t}_ins ON dq_ctl.{t} FOR INSERT TO dq_api
  WITH CHECK (EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.unit_key = {t}.unit_key));
DROP POLICY IF EXISTS {t}_sys ON dq_ctl.{t};
CREATE POLICY {t}_sys ON dq_ctl.{t} FOR ALL TO dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_auditor
  USING (true) WITH CHECK (true);""" for t in ("decisions", "quarantine", "unit_events", "deny"))
    return f"""
CREATE SCHEMA IF NOT EXISTS dq_ctl;
REVOKE ALL ON SCHEMA dq_ctl FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA dq_ctl REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Part E §7: the explanation shown to a person is bound to their decision by its two hashes (never its text)
CREATE OR REPLACE FUNCTION dq_ctl.expl_ref_ok(p jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT p IS NULL OR (jsonb_typeof(p) = 'object'
     AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['facts_hash','text_hash']
     AND coalesce(p->>'facts_hash', '') ~ '^[0-9a-f]{{64}}$' AND coalesce(p->>'text_hash', '') ~ '^[0-9a-f]{{64}}$')
$$;

CREATE OR REPLACE FUNCTION dq_ctl.reject_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  RAISE EXCEPTION 'append-only table %: % is not permitted', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- lifecycle -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dq_ctl.allowed_transitions(
  from_state text NOT NULL, to_state text NOT NULL, via text NOT NULL,
  PRIMARY KEY (from_state, to_state, via));
TRUNCATE dq_ctl.allowed_transitions;
INSERT INTO dq_ctl.allowed_transitions VALUES
{sql_allowed_transitions_values()};

CREATE TABLE IF NOT EXISTS dq_ctl.batches(
  batch_id text PRIMARY KEY, pipeline_id text NOT NULL, node text NOT NULL,
  sealed boolean NOT NULL DEFAULT false, load_set jsonb, fp_epoch int,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS dq_ctl.units(
  unit_key text PRIMARY KEY,
  batch_id text NOT NULL REFERENCES dq_ctl.batches(batch_id),
  pipeline_id text NOT NULL, node text NOT NULL,
  program_code text NOT NULL, authorities text[] NOT NULL, partition_key text NOT NULL,
  state text NOT NULL, state_version int NOT NULL DEFAULT 0,
  fingerprint text, fp_epoch int, max_row_seq bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (batch_id, program_code, partition_key));

CREATE TABLE IF NOT EXISTS dq_ctl.unit_events(
  event_id bigserial PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  from_state text, to_state text NOT NULL, via text NOT NULL, state_version int NOT NULL,
  actor text NOT NULL, payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (unit_key, state_version));

-- loads (registration is append-only events; current state is derived) ----------
CREATE TABLE IF NOT EXISTS dq_ctl.loads_events(
  id bigserial PRIMARY KEY, load_id text NOT NULL, pipeline_id text NOT NULL,
  relation text NOT NULL, batch_id text NOT NULL, event text NOT NULL
    CHECK (event IN ('REGISTERED','COMMITTED','FROZEN')),
  landing_version text, landing_digest text, at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (load_id, event));

-- file registry (append-only): direction + interchange keys for duplicate detection ---
CREATE TABLE IF NOT EXISTS dq_ctl.files(
  file_id text PRIMARY KEY, sha256 text NOT NULL, partner text NOT NULL, direction text NOT NULL
    CHECK (direction IN ('INBOUND_PRE_TRANSLATOR','INBOUND_POST_TRANSLATOR','OUTBOUND')),
  format text NOT NULL, batch_id text NOT NULL, interchange_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
  translator_status jsonb, received_at timestamptz NOT NULL DEFAULT clock_timestamp());

-- decision tokens (S1): one signed JWS may cover a cluster; jti is single-use ----------
CREATE TABLE IF NOT EXISTS dq_ctl.decision_tokens(
  token_id text PRIMARY KEY, person_id text NOT NULL, jws text NOT NULL, jti text NOT NULL UNIQUE,
  auth_time timestamptz NOT NULL, kid text NOT NULL, challenge text NOT NULL, step_up boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp());

-- decisions: one row per (unit, round, person); bound to fingerprint + qset + dispositions
CREATE TABLE IF NOT EXISTS dq_ctl.decisions(
  decision_id text PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  round int NOT NULL, seat text NOT NULL CHECK (seat IN ('MAKER','CHECKER','COSIGNER')),
  person_id text NOT NULL, authority text NOT NULL, decision text NOT NULL,
  rationale text NOT NULL CHECK (length(rationale) >= 20),
  token_id text NOT NULL REFERENCES dq_ctl.decision_tokens(token_id),
  bound_fingerprint text NOT NULL, fp_epoch int NOT NULL,
  qset_hash text NOT NULL, disposition_vector jsonb NOT NULL, maker_decision_id text,
  explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref)),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (unit_key, round, person_id),
  UNIQUE (unit_key, round, seat, authority),
  UNIQUE (token_id, unit_key));
ALTER TABLE dq_ctl.decisions ADD COLUMN IF NOT EXISTS explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref));

CREATE TABLE IF NOT EXISTS dq_ctl.quarantine_proposals(
  decision_id text PRIMARY KEY REFERENCES dq_ctl.decisions(decision_id), row_seqs jsonb NOT NULL,
  qset_hash text NOT NULL);

CREATE TABLE IF NOT EXISTS dq_ctl.attestations(
  att_id bigserial PRIMARY KEY, subject_type text NOT NULL, subject_id text NOT NULL,
  verifier_identity text NOT NULL, result text NOT NULL CHECK (result IN ('VALID','INVALID')),
  detail jsonb NOT NULL DEFAULT '{{}}'::jsonb, at timestamptz NOT NULL DEFAULT clock_timestamp());

-- roster (S4): append-only; revocation is a new row ----------------------------------
CREATE TABLE IF NOT EXISTS dq_ctl.roster(
  id bigserial PRIMARY KEY, person_id text NOT NULL, duty text NOT NULL, authority text NOT NULL,
  action text NOT NULL CHECK (action IN ('GRANT','REVOKE')), decision_ref text NOT NULL,
  org text,                                  -- the person's organisation for this grant (e.g. STATE | VENDOR)
  at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE OR REPLACE VIEW dq_ctl.roster_current AS
  SELECT person_id, duty, authority, org FROM (
    SELECT DISTINCT ON (person_id, duty, authority) person_id, duty, authority, action, org
    FROM dq_ctl.roster ORDER BY person_id, duty, authority, id DESC) r
  WHERE action = 'GRANT';

-- evaluation results (scoped) -------------------------------------------------------
CREATE TABLE IF NOT EXISTS dq_ctl.results(
  id bigserial PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  eval_version int NOT NULL, authority text NOT NULL, rule_id text NOT NULL, rule_hash text NOT NULL,
  check_class text NOT NULL, priority text NOT NULL, disposition text NOT NULL,
  evaluated bigint, failed bigint, metric numeric, threshold jsonb,
  sample_tokens jsonb NOT NULL DEFAULT '[]'::jsonb, ruleset_version text,
  at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS dq_ctl.abc(
  id bigserial PRIMARY KEY, batch_id text NOT NULL, authority text NOT NULL, hop text NOT NULL,
  measure text NOT NULL, program_code text, submitter_id text, src_in numeric, tgt_out numeric,
  rejected numeric, quarantined numeric, deduplicated numeric, filtered numeric, held numeric,
  residual numeric, disposition text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(),
  gating boolean NOT NULL DEFAULT true);
-- Part E §3.1: a non-gating hop (empty delivery, parallel run) is recorded but never raises the batch floor
ALTER TABLE dq_ctl.abc ADD COLUMN IF NOT EXISTS gating boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS dq_ctl.measure_results(
  id bigserial PRIMARY KEY, batch_id text NOT NULL, node text NOT NULL, authority text NOT NULL, rule_id text NOT NULL,
  rule_hash text NOT NULL, numerator bigint, denominator bigint, value numeric, thresholds jsonb,
  disposition text NOT NULL, period_key text, prior_value numeric, long_disposition text,
  at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS dq_ctl.deny(
  deny_id bigserial PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  reason text NOT NULL, mechanism text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS dq_ctl.quarantine(
  id bigserial PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  row_seq bigint NOT NULL, scope text NOT NULL CHECK (scope IN ('ROW','CHAIN','UNIT')),
  decision_id text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (unit_key, row_seq));

CREATE TABLE IF NOT EXISTS dq_ctl.manifest_versions(
  id bigserial PRIMARY KEY, dp text NOT NULL, version int NOT NULL, content_hash text NOT NULL,
  content jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (dp, version));

-- a manifest version is RESERVED in the CP before the DP is written, so a crash between the DP
-- commit and the CP record can never cause a version number to be reused (SEC-W1-6)
CREATE TABLE IF NOT EXISTS dq_ctl.manifest_reservations(
  dp text NOT NULL, version int NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (dp, version));

CREATE TABLE IF NOT EXISTS dq_ctl.registry_events(
  id bigserial PRIMARY KEY, artifact_kind text NOT NULL, artifact_id text NOT NULL,
  semantic_hash text NOT NULL, state text NOT NULL CHECK (state IN ('PENDING','APPROVED','REVOKED')),
  decision_ref text, rule_meta jsonb, at timestamptz NOT NULL DEFAULT clock_timestamp());

-- audit chain ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dq_ctl.audit_log(
  seq bigint PRIMARY KEY, event text NOT NULL, payload_digest text NOT NULL,
  prev_hash text NOT NULL, row_hash text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS dq_ctl.audit_head(
  id int PRIMARY KEY CHECK (id = 1), seq bigint NOT NULL, head_hash text NOT NULL);
INSERT INTO dq_ctl.audit_head VALUES (1, 0, repeat('0',64)) ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION dq_ctl.append_audit(p_event text, p_payload_digest text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE h record; nh text; ns bigint;
BEGIN
  SELECT * INTO h FROM dq_ctl.audit_head WHERE id = 1 FOR UPDATE;   -- serialises writers
  ns := h.seq + 1;
  nh := encode(sha256(convert_to(h.head_hash || '|' || ns || '|' || p_event || '|' || p_payload_digest, 'UTF8')), 'hex');
  INSERT INTO dq_ctl.audit_log(seq, event, payload_digest, prev_hash, row_hash)
    VALUES (ns, p_event, p_payload_digest, h.head_hash, nh);
  UPDATE dq_ctl.audit_head SET seq = ns, head_hash = nh WHERE id = 1;
  RETURN ns;
END $$;

-- the single writer of unit state (callable ONLY through the role-bound wrappers) ----
DROP FUNCTION IF EXISTS dq_ctl.transition(text,int,text,text,text,jsonb);
CREATE OR REPLACE FUNCTION dq_ctl._transition(
  p_unit text, p_expected_version int, p_to text, p_via text, p_actor text, p_payload jsonb)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE u record; nv int;
BEGIN
  SELECT * INTO u FROM dq_ctl.units WHERE unit_key = p_unit FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown unit %', p_unit USING ERRCODE = 'no_data_found'; END IF;
  IF u.state_version <> p_expected_version THEN
    RAISE EXCEPTION 'CONFLICT expected % found %', p_expected_version, u.state_version
      USING ERRCODE = 'serialization_failure';
  END IF;
  IF u.state IN ({terminal}) THEN
    RAISE EXCEPTION 'unit % is terminal (%)', p_unit, u.state USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM dq_ctl.allowed_transitions
                 WHERE from_state = u.state AND to_state = p_to AND via = p_via) THEN
    RAISE EXCEPTION 'illegal transition % -> % via %', u.state, p_to, p_via USING ERRCODE = 'check_violation';
  END IF;
  nv := u.state_version + 1;
  INSERT INTO dq_ctl.unit_events(unit_key, from_state, to_state, via, state_version, actor, payload)
    VALUES (p_unit, u.state, p_to, p_via, nv, p_actor, coalesce(p_payload, '{{}}'::jsonb));
  UPDATE dq_ctl.units SET state = p_to, state_version = nv WHERE unit_key = p_unit;
  PERFORM dq_ctl.append_audit('TRANSITION', encode(sha256(convert_to(
     p_unit || '|' || u.state || '|' || p_to || '|' || p_via || '|' || nv || '|' || p_actor, 'UTF8')), 'hex'));
  RETURN nv;
END $$;

-- guard: unit heads can only change through transition() --------------------------
CREATE OR REPLACE FUNCTION dq_ctl.units_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'units are never deleted'; END IF;
  IF NEW.state IS DISTINCT FROM OLD.state OR NEW.state_version IS DISTINCT FROM OLD.state_version THEN
    IF NOT EXISTS (SELECT 1 FROM dq_ctl.unit_events
                   WHERE unit_key = NEW.unit_key AND state_version = NEW.state_version
                     AND to_state = NEW.state) THEN
      RAISE EXCEPTION 'unit head changed without a matching event (use dq_ctl.transition)';
    END IF;
  END IF;
  IF OLD.fingerprint IS NOT NULL AND (NEW.fingerprint IS DISTINCT FROM OLD.fingerprint
     OR NEW.fp_epoch IS DISTINCT FROM OLD.fp_epoch OR NEW.max_row_seq IS DISTINCT FROM OLD.max_row_seq) THEN
    RAISE EXCEPTION 'unit fingerprint is write-once (re-seal creates a new unit)' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.unit_key <> OLD.unit_key OR NEW.authorities <> OLD.authorities
     OR NEW.program_code <> OLD.program_code OR NEW.partition_key <> OLD.partition_key THEN
    RAISE EXCEPTION 'unit identity is immutable';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION dq_ctl.units_insert_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF NEW.state <> 'LANDED' OR NEW.state_version <> 0 OR NEW.fingerprint IS NOT NULL OR NEW.max_row_seq IS NOT NULL THEN
    RAISE EXCEPTION 'units are born LANDED at version 0 with no fingerprint' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS units_insert_guard ON dq_ctl.units;
CREATE TRIGGER units_insert_guard BEFORE INSERT ON dq_ctl.units
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.units_insert_guard();
DROP TRIGGER IF EXISTS units_guard ON dq_ctl.units;
CREATE TRIGGER units_guard BEFORE UPDATE OR DELETE ON dq_ctl.units
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.units_guard();

-- scope + RLS (S9) -----------------------------------------------------------------
CREATE OR REPLACE FUNCTION dq_ctl.set_scope(p_authorities text[]) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT set_config('dq.scope', array_to_string(p_authorities, ','), true)::text IS NOT NULL AND true;
$$;
CREATE OR REPLACE FUNCTION dq_ctl.in_scope(p_auth text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT coalesce(p_auth = ANY (string_to_array(nullif(current_setting('dq.scope', true), ''), ',')), false);
$$;
CREATE OR REPLACE FUNCTION dq_ctl.unit_in_scope(p_auths text[]) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT coalesce(p_auths <@ string_to_array(nullif(current_setting('dq.scope', true), ''), ','), false);
$$;

ALTER TABLE dq_ctl.units ENABLE ROW LEVEL SECURITY;  ALTER TABLE dq_ctl.units FORCE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.results ENABLE ROW LEVEL SECURITY; ALTER TABLE dq_ctl.results FORCE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.abc ENABLE ROW LEVEL SECURITY;     ALTER TABLE dq_ctl.abc FORCE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.measure_results ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS measure_scope ON dq_ctl.measure_results;
CREATE POLICY measure_scope ON dq_ctl.measure_results FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS measure_sys ON dq_ctl.measure_results;
CREATE POLICY measure_sys ON dq_ctl.measure_results FOR ALL TO dq_eval_svc, dq_publish_svc, dq_verify_svc, dq_auditor
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS units_scope ON dq_ctl.units;
CREATE POLICY units_scope ON dq_ctl.units FOR SELECT USING (dq_ctl.unit_in_scope(authorities));
DROP POLICY IF EXISTS units_sys ON dq_ctl.units;
CREATE POLICY units_sys ON dq_ctl.units FOR ALL TO dq_seal_svc, dq_eval_svc, dq_publish_svc, dq_verify_svc
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS units_owner ON dq_ctl.units;
CREATE POLICY units_owner ON dq_ctl.units FOR ALL TO dq_owner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS results_owner ON dq_ctl.results;
CREATE POLICY results_owner ON dq_ctl.results FOR ALL TO dq_owner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS abc_owner ON dq_ctl.abc;
CREATE POLICY abc_owner ON dq_ctl.abc FOR ALL TO dq_owner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS results_scope ON dq_ctl.results;
CREATE POLICY results_scope ON dq_ctl.results FOR SELECT USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS results_sys ON dq_ctl.results;
CREATE POLICY results_sys ON dq_ctl.results FOR ALL TO dq_eval_svc, dq_verify_svc, dq_publish_svc USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS abc_scope ON dq_ctl.abc;
CREATE POLICY abc_scope ON dq_ctl.abc FOR SELECT USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS abc_sys ON dq_ctl.abc;
CREATE POLICY abc_sys ON dq_ctl.abc FOR ALL TO dq_eval_svc, dq_seal_svc, dq_verify_svc, dq_publish_svc USING (true) WITH CHECK (true);

-- decision-side tables are scoped by their unit's authorities for the api (SEC-W1-7, partial)
{decision_rls}

-- pre-split (AUTH_INTAKE) hop VERDICTS for viewers of in-scope units: no counts cross authorities (DESIGN §5.3).
-- Part F E-08: `gating` is returned so the decision path demands the intake duty only for a GATING failure (the one
-- definition, dq_ctl.abc_gating_failures); non-gating verdicts (an empty delivery) stay visible on the card.
DROP FUNCTION IF EXISTS dq_ctl.intake_verdicts(text);
CREATE OR REPLACE FUNCTION dq_ctl.intake_verdicts(p_batch text)
RETURNS TABLE(batch_id text, hop text, measure text, disposition text, gating boolean)
LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT a.batch_id, a.hop, a.measure, a.disposition, a.gating FROM dq_ctl.abc a
  WHERE a.authority = 'AUTH_INTAKE' AND (p_batch IS NULL OR a.batch_id = p_batch)
    AND EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.batch_id = a.batch_id
                AND u.authorities <@ string_to_array(nullif(current_setting('dq.scope', true), ''), ','));
$$;

{append_only_triggers}

-- grants (least privilege; no service holds CREATE) -----------------------------------
GRANT USAGE ON SCHEMA dq_ctl TO {", ".join(SERVICE_ROLES)};
GRANT SELECT ON ALL TABLES IN SCHEMA dq_ctl TO dq_api, dq_verify_svc, dq_publish_svc, dq_eval_svc;
GRANT SELECT ON dq_ctl.audit_log, dq_ctl.audit_head TO dq_auditor;
REVOKE SELECT ON dq_ctl.audit_log, dq_ctl.audit_head FROM dq_api;   -- /audit runs as dq_auditor
GRANT INSERT ON dq_ctl.batches, dq_ctl.units, dq_ctl.loads_events, dq_ctl.abc TO dq_seal_svc;
GRANT SELECT ON dq_ctl.batches, dq_ctl.units, dq_ctl.loads_events, dq_ctl.allowed_transitions TO dq_seal_svc, dq_eval_svc, dq_publish_svc;
GRANT UPDATE (sealed, load_set, fp_epoch) ON dq_ctl.batches TO dq_seal_svc;
GRANT UPDATE (fingerprint, fp_epoch, max_row_seq) ON dq_ctl.units TO dq_seal_svc;
GRANT INSERT ON dq_ctl.results, dq_ctl.abc, dq_ctl.measure_results TO dq_eval_svc;
GRANT INSERT ON dq_ctl.decisions, dq_ctl.decision_tokens, dq_ctl.quarantine, dq_ctl.quarantine_proposals TO dq_api;
GRANT INSERT, SELECT ON dq_ctl.files TO dq_seal_svc;
GRANT INSERT ON dq_ctl.attestations, dq_ctl.deny TO dq_verify_svc;
GRANT INSERT ON dq_ctl.manifest_versions TO dq_publish_svc;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA dq_ctl TO {", ".join(SERVICE_ROLES)};
REVOKE ALL ON FUNCTION dq_ctl._transition(text,int,text,text,text,jsonb) FROM PUBLIC;
{transition_wrappers_ddl()}
REVOKE SELECT ON dq_ctl.decision_tokens FROM dq_api;            -- the api never reads stored JWS back
GRANT INSERT ON dq_ctl.manifest_reservations TO dq_publish_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.append_audit(text,text) TO dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.set_scope(text[]) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.intake_verdicts(text) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.in_scope(text), dq_ctl.unit_in_scope(text[]) TO {", ".join(SERVICE_ROLES)};
"""


OWNER = "dq_owner"   # NOLOGIN, non-superuser owner of every CP object (no human holds it)

# Roles are CLUSTER-wide: CREATE / ALTER ROLE and GRANT / REVOKE dq_owner TO current_user update shared catalog rows
# (pg_authid, pg_auth_members) that every database's apply touches. Two applies running at once (two CP databases on
# one cluster, e.g. parallel test sessions) then collide with "tuple concurrently updated". apply() therefore holds a
# session advisory lock on a FIXED key for the whole apply, until its transaction has committed (the catalog rows stay
# locked until commit). Advisory locks are scoped to the database they are taken in, so the lock is taken in ONE
# fixed database of the cluster (CLUSTER_LOCK_DB) on a separate connection; a lock taken inside each target database
# would never contend.
CLUSTER_LOCK_KEY = 0x44515F524F4C45      # 'DQ_ROLE'
CLUSTER_LOCK_DB = "postgres"


@contextlib.contextmanager
def cluster_lock(conn, lock_db: str = CLUSTER_LOCK_DB):
    """Hold the cluster-wide schema/role lock (pg_advisory_lock on CLUSTER_LOCK_KEY in `lock_db`) on a separate
    connection made with `conn`'s host, port and user. Released after the block (after the caller's commit)."""
    import psycopg
    info = conn.info
    kw = {"host": info.host, "port": info.port, "user": info.user, "dbname": lock_db, "autocommit": True}
    if info.password:
        kw["password"] = info.password
    with psycopg.connect(**kw) as lk:
        lk.execute("SELECT pg_advisory_lock(%s)", (CLUSTER_LOCK_KEY,))
        try:
            yield
        finally:
            lk.execute("SELECT pg_advisory_unlock(%s)", (CLUSTER_LOCK_KEY,))


def apply(conn) -> None:
    """Bootstrap roles as the cluster admin, then create every object AS dq_owner.

    SECURITY DEFINER functions therefore run with dq_owner's (non-superuser) rights,
    and FORCE RLS applies to the owner as well. Serialised across the cluster (cluster_lock): the role DDL is
    cluster-wide and its catalog rows stay locked until this transaction commits.
    """
    with cluster_lock(conn):
        _apply(conn)


def _apply(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(roles_ddl())
        cur.execute(f"DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{OWNER}') "
                    f"THEN CREATE ROLE {OWNER} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END$$;")
        cur.execute("SELECT current_database()")
        db = cur.fetchone()[0]
        cur.execute(f'GRANT CREATE ON DATABASE "{db}" TO {OWNER}')
        cur.execute(f"GRANT {OWNER} TO current_user")     # so the bootstrap admin may SET ROLE
        cur.execute(f"SET ROLE {OWNER}")
        cur.execute(schema_ddl())
        from dqagent.cp.agent_schema import agent_ddl
        cur.execute(agent_ddl())                         # agent state / blackboard / memory (Part C WS3)
        from dqagent.cp.governance_schema import governance_ddl
        cur.execute(governance_ddl())                    # two-person configuration governance (Part D)
        from dqagent.cp.governance_schema import parte_ddl
        cur.execute(parte_ddl())                         # earned autonomy, registry, reconciliation, monitor (Part E)
        from dqagent.cp.trace_schema import trace_ddl
        cur.execute(trace_ddl())                         # runs, spans, release checks, LLM calls (Part F P1-1)
        cur.execute("RESET ROLE")
        cur.execute(f"REVOKE {OWNER} FROM current_user")   # the bootstrap login keeps no path into the owner
    conn.commit()
