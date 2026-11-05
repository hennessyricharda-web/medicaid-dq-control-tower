"""Agent observability schema (Part F P1-1 A, F-05 backend): runs, spans, release checks, LLM calls, guardrail events and
explanations, plus the correlation columns on the existing tables. Applied by cp/schema._apply after parte_ddl().

Order inside trace_ddl (plan C3): helper functions, then tables, then append-only / RLS, then grants, then the
correlation columns on existing tables. The role dq_trace_svc is created with every service role (cp/schema.SERVICE_ROLES)
and is the ONLY role that inserts trace rows (ADJ-7).

Reading (I1): a dq_api row needs a NON-EMPTY authority set inside the caller's scope (`unit_in_scope` alone is true for
an empty array). Engine-wide rows (empty authorities: NIGHT, PUBLISH_CYCLE and RECOVER roots, ...) are read only on the
audit / operations path, as dq_auditor, after the api has checked the duty.

Correlation (`trace_id`, `span_id`, `code_sha256`) is stamped by column DEFAULTs from the session settings dq.trace_id,
dq.span_id and dq.code_sha256 that CP.connect_as sets. They are never part of a hash (bb_hash, the audit chain) and never
written into a payload.
"""
from __future__ import annotations

RUN_KINDS = ("NIGHT", "PIPELINE_RUN", "REEVALUATE", "PUBLISH_CYCLE", "DECISION", "APPLY_CHANGE", "BACKTEST", "EXPLAIN",
             "RECOVER")
GUARDRAIL_CODES = ("MASKED", "SUPPRESSED", "PART2_WITHHELD", "CHECK_REJECTED", "ADVICE_BLOCKED", "CAUSAL_BLOCKED",
                   "FALLBACK", "FLOOR_APPLIED", "CEILING_APPLIED", "ENTITLEMENT_REFUSED", "SCOPE_DENIED",
                   "BODY_REJECTED", "EXPLANATION_MISMATCH", "STEP_UP_REQUIRED",
                   "RECOVERY_REFUSED")              # Part F R4-1 / N-T0-1: a refused ABC recovery
LLM_TRANSPORTS = ("none", "http", "scripted", "recorded", "fixed")   # = the union of dqagent.deploy.TRANSPORTS
TRACE_TABLES = ("runs", "run_events", "spans", "release_checks", "llm_calls", "guardrail_events", "explain_facts",
                "prompt_versions", "explanations",
                "run_scopes")      # F-34: the union of the authorities a root touched (written once, at its flush)
SCOPED_TABLES = ("runs", "spans", "release_checks", "llm_calls", "guardrail_events", "explanations")
CORRELATED = ("unit_events", "results", "abc", "measure_results", "agent_blackboard", "audit_log", "veto_windows",
              "decisions", "attestations", "manifest_versions", "monitor_metrics")
REFS_PREFIXES = ("batch", "rel", "rule", "unit", "file", "cr", "dec", "pipeline", "taskgate", "abc", "results",
                 "decisions", "units", "trace",
                 # I5: the subjects the explainer and the guardrails use (guardrail_events.subject is now checked)
                 "feed", "nightly", "autonomy", "parallel", "cde", "lookback", "element")
ATTR_KEY = r"^[a-z][a-z0-9_.]{0,63}$"
# I5: span attribute strings are a CLOSED vocabulary per key (no free text, identifiers or e-mail addresses). A new
# string attribute is added here, in one place; numbers, booleans and counts need no entry.
ATTR_VOCAB = {
    "ingest_state": ("NEW", "SEALED", "PARTIAL"),
    "source_mode": ("TEMPLATE", "SCRIPTED", "RECORDED", "LIVE"),
    "transport": ("none", "http", "scripted", "recorded", "fixed"),
    "verdict": ("PASS", "REJECT", "NOT_CHECKED", "NO_OUTPUT"),
    "fact_check": ("TEMPLATE", "PASS", "FALLBACK"),
    # Part F P1-3 (K2b-3, RESOLVED 2026-10-26): the PIPELINE_RUN span's advisory gate-check attribute. Two values
    # only, not three: K2 is advisory-only (block-mode promotion is K3's scope), so a real HOLD value does not
    # exist yet -- reserving it now would let K2-era code claim a capability it doesn't have. WOULD_HOLD is the
    # honest, K2-scoped label for "this gate would have held in block mode"; HOLD is reserved when K3 builds
    # block-mode for real.
    "gate_verdict": ("PASS", "WOULD_HOLD"),
}
ATTR_VALUES = tuple(sorted({v for vs in ATTR_VOCAB.values() for v in vs}))
SPAN_NAME_RE = r"^[a-z][A-Za-z0-9_.:@\[\] -]{0,119}$"
# a release reason starts with its publisher clause (1..12, with 2b/2c/2d) or `stale`, then text without controls
# (unbounded here: a Postgres regex caps a bound at 255; the length is checked beside it, REASON_MAX)
REASON_RE = r"^(1[0-2]?|2[bcd]?|[3-9]|stale): [^[:cntrl:]]+$"
REASON_MAX = 300
COUNT_KEY = r"^[A-Za-z0-9_.:-]{1,64}$"
REF_RE = r"^(" + "|".join(REFS_PREFIXES) + r"):[A-Za-z0-9_.:@|/+=-]{1,200}$"
TRACE_ID_RE = r"^[A-Z]{3,5}-[A-Za-z0-9_.:-]{1,120}$"
SPAN_ID_RE = r"^[0-9a-f]{16}$"


def _l(xs) -> str:
    return ",".join(f"'{x}'" for x in xs)


def trace_ddl() -> str:
    from dqagent.cp.governance_schema import VETO_WINDOW_STATUS_GRANT, VETO_WINDOW_STATUS_VIEW
    scoped_policies = "\n".join(f"""
DROP POLICY IF EXISTS {t}_api ON dq_ctl.{t};
CREATE POLICY {t}_api ON dq_ctl.{t} FOR SELECT TO dq_api
  USING (cardinality(authorities) > 0 AND dq_ctl.unit_in_scope(authorities));""" for t in SCOPED_TABLES)
    sys_policies = "\n".join(f"""
DROP POLICY IF EXISTS {t}_trace ON dq_ctl.{t};
CREATE POLICY {t}_trace ON dq_ctl.{t} FOR ALL TO dq_trace_svc USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS {t}_audit ON dq_ctl.{t};
CREATE POLICY {t}_audit ON dq_ctl.{t} FOR SELECT TO dq_auditor USING (true);""" for t in TRACE_TABLES)
    correlation = "\n".join(f"""
ALTER TABLE dq_ctl.{t} ADD COLUMN IF NOT EXISTS trace_id text DEFAULT nullif(current_setting('dq.trace_id', true), '');
ALTER TABLE dq_ctl.{t} ADD COLUMN IF NOT EXISTS span_id text DEFAULT nullif(current_setting('dq.span_id', true), '');
ALTER TABLE dq_ctl.{t} ADD COLUMN IF NOT EXISTS code_sha256 text
  DEFAULT nullif(current_setting('dq.code_sha256', true), '');""" for t in CORRELATED)
    tables = ", ".join(f"dq_ctl.{t}" for t in TRACE_TABLES)
    others = "dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_auditor, dq_triage_svc, " \
             "dq_registry_svc, dq_monitor_svc"
    return f"""
-- ===== helpers FIRST (C3): every CHECK below uses them ===========================================================
CREATE OR REPLACE FUNCTION dq_ctl.counts_only(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, pg_temp AS $$
  SELECT jsonb_typeof(p) = 'object' AND (SELECT count(*) FROM jsonb_object_keys(p)) <= 64
     AND NOT EXISTS (SELECT 1 FROM jsonb_each(p) e WHERE e.key !~ '{COUNT_KEY}'
          OR jsonb_typeof(e.value) <> 'number' OR (e.value #>> '{{}}')::numeric < 0
          OR (e.value #>> '{{}}')::numeric <> trunc((e.value #>> '{{}}')::numeric)) $$;
CREATE OR REPLACE FUNCTION dq_ctl.span_attrs_ok(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT jsonb_typeof(p) = 'object' AND (SELECT count(*) FROM jsonb_object_keys(p)) <= 64
     AND NOT EXISTS (SELECT 1 FROM jsonb_each(p) e WHERE e.key !~ '{ATTR_KEY}'
          OR NOT (jsonb_typeof(e.value) IN ('number','boolean')
                  OR (jsonb_typeof(e.value) = 'string' AND (e.value #>> '{{}}') = ANY (ARRAY[{_l(ATTR_VALUES)}]))
                  OR (jsonb_typeof(e.value) = 'object' AND dq_ctl.counts_only(e.value)))) $$;
CREATE OR REPLACE FUNCTION dq_ctl.reasons_ok(p text[]) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(cardinality(p), 0) <= 32
     AND coalesce((SELECT bool_and(r ~ '{REASON_RE}' AND length(r) <= {REASON_MAX}) FROM unnest(p) r), true) $$;
CREATE OR REPLACE FUNCTION dq_ctl.scope_overlaps(p_auths text[]) RETURNS boolean LANGUAGE sql STABLE
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT coalesce(cardinality(p_auths) > 0
                  AND p_auths && string_to_array(nullif(current_setting('dq.scope', true), ''), ','), false) $$;
CREATE OR REPLACE FUNCTION dq_ctl.span_refs_ok(p text[]) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(cardinality(p), 0) <= 32 AND coalesce((SELECT bool_and(r ~ '{REF_RE}') FROM unnest(p) r), true) $$;

-- ===== tables ====================================================================================================
CREATE TABLE IF NOT EXISTS dq_ctl.runs(trace_id text PRIMARY KEY CHECK (trace_id ~ '{TRACE_ID_RE}'),
  kind text NOT NULL CHECK (kind IN ({_l(RUN_KINDS)})),
  parent_trace_id text, state text NOT NULL, pipeline_id text, night_date date, attempt int NOT NULL DEFAULT 1,
  person_id text, config_digest text NOT NULL, code_digest text NOT NULL, profile_digests jsonb NOT NULL DEFAULT '{{}}',
  deployment text NOT NULL, source_mode text, authorities text[] NOT NULL DEFAULT '{{}}',
  started_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS dq_ctl.run_events(id bigserial PRIMARY KEY, trace_id text NOT NULL REFERENCES dq_ctl.runs(trace_id),
  event text NOT NULL CHECK (event IN ('STARTED','RESUMED','COMPLETED','FAILED')), error_class text,
  at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS dq_ctl.spans(span_id text PRIMARY KEY CHECK (span_id ~ '{SPAN_ID_RE}'),
  trace_id text NOT NULL REFERENCES dq_ctl.runs(trace_id), parent_span_id text, ordinal int NOT NULL, name text NOT NULL,
  seat text, subject text CHECK (subject IS NULL OR dq_ctl.span_refs_ok(ARRAY[subject])),
  authorities text[] NOT NULL DEFAULT '{{}}', status text NOT NULL CHECK (status IN ('OK','ERROR','SKIPPED')),
  started_at timestamptz NOT NULL, ended_at timestamptz NOT NULL,
  attrs jsonb NOT NULL DEFAULT '{{}}' CHECK (dq_ctl.span_attrs_ok(attrs)),
  refs text[] NOT NULL DEFAULT '{{}}' CHECK (dq_ctl.span_refs_ok(refs)));
CREATE INDEX IF NOT EXISTS spans_trace ON dq_ctl.spans(trace_id, ordinal);
ALTER TABLE dq_ctl.spans DROP CONSTRAINT IF EXISTS spans_name_check;
ALTER TABLE dq_ctl.spans ADD CONSTRAINT spans_name_check CHECK (name ~ '{SPAN_NAME_RE}');
CREATE TABLE IF NOT EXISTS dq_ctl.release_checks(id bigserial PRIMARY KEY, span_id text NOT NULL, unit_key text NOT NULL,
  ok boolean NOT NULL, reasons text[] NOT NULL, authorities text[] NOT NULL);
CREATE INDEX IF NOT EXISTS release_checks_unit ON dq_ctl.release_checks(unit_key, id);
ALTER TABLE dq_ctl.release_checks DROP CONSTRAINT IF EXISTS release_checks_reasons_check;
ALTER TABLE dq_ctl.release_checks ADD CONSTRAINT release_checks_reasons_check CHECK (dq_ctl.reasons_ok(reasons));
CREATE TABLE IF NOT EXISTS dq_ctl.llm_calls(call_id bigserial PRIMARY KEY, trace_id text NOT NULL, span_id text NOT NULL,
  subject text NOT NULL, authorities text[] NOT NULL, kind text NOT NULL, provider text NOT NULL, model text,
  transport text NOT NULL CHECK (transport IN ({_l(LLM_TRANSPORTS)})),
  source_mode text NOT NULL CHECK (source_mode IN ('TEMPLATE','SCRIPTED','RECORDED','LIVE')),
  prompt_version text NOT NULL, check_version text NOT NULL, template_version text NOT NULL, input_digest text,
  facts_hash text NOT NULL, output_text text, text_hash text,
  verdict text NOT NULL CHECK (verdict IN ('PASS','REJECT','NOT_CHECKED','NO_OUTPUT')),
  reason_codes text[] NOT NULL DEFAULT '{{}}', token_sha256 text[] NOT NULL DEFAULT '{{}}',
  input_tokens int, output_tokens int, latency_ms int, finish_reason text, at timestamptz NOT NULL DEFAULT clock_timestamp());
-- an already-applied schema accepts every transport the deploy allow-list knows (fixed: test deployment only)
ALTER TABLE dq_ctl.llm_calls DROP CONSTRAINT IF EXISTS llm_calls_transport_check;
ALTER TABLE dq_ctl.llm_calls ADD CONSTRAINT llm_calls_transport_check CHECK (transport IN ({_l(LLM_TRANSPORTS)}));
CREATE TABLE IF NOT EXISTS dq_ctl.guardrail_events(id bigserial PRIMARY KEY, trace_id text, span_id text,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  code text NOT NULL CHECK (code IN ({_l(GUARDRAIL_CODES)})),
  subject text NOT NULL, seat text, authorities text[] NOT NULL,
  detail jsonb NOT NULL DEFAULT '{{}}' CHECK (dq_ctl.counts_only(detail)));
ALTER TABLE dq_ctl.guardrail_events DROP CONSTRAINT IF EXISTS guardrail_events_subject_check;
ALTER TABLE dq_ctl.guardrail_events ADD CONSTRAINT guardrail_events_subject_check
  CHECK (dq_ctl.span_refs_ok(ARRAY[subject]));
CREATE TABLE IF NOT EXISTS dq_ctl.explain_facts(facts_hash text PRIMARY KEY, facts jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS dq_ctl.prompt_versions(prompt_version text PRIMARY KEY, system_prompt text NOT NULL,
  check_version text NOT NULL, template_version text NOT NULL, first_seen timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS dq_ctl.explanations(subject text NOT NULL, authorities text[] NOT NULL, facts_hash text NOT NULL,
  provider_config_digest text NOT NULL, prompt_version text NOT NULL, check_version text NOT NULL,
  template_version text NOT NULL, text text NOT NULL, text_hash text NOT NULL, source_mode text NOT NULL,
  claims jsonb NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (subject, facts_hash, provider_config_digest, prompt_version, check_version, template_version));
CREATE TABLE IF NOT EXISTS dq_ctl.run_scopes(trace_id text PRIMARY KEY REFERENCES dq_ctl.runs(trace_id),
  authorities text[] NOT NULL CHECK (cardinality(authorities) > 0));
-- I4: trace ids are minted from one sequence (atomic across processes)
CREATE SEQUENCE IF NOT EXISTS dq_ctl.trace_id_seq;

-- ===== append-only, RLS ==========================================================================================
DO $$DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[{_l(TRACE_TABLES)}] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_mut', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON dq_ctl.%I FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_mut', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_trunc', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON dq_ctl.%I FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_trunc', t);
    EXECUTE format('REVOKE ALL ON dq_ctl.%I FROM PUBLIC', t);
    EXECUTE format('REVOKE ALL ON dq_ctl.%I FROM {others}', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END$$;
{scoped_policies}
-- F-34: a root is also readable when the authorities it TOUCHED overlap the caller's scope (a scoped reviewer finds the
-- night they took part in); its spans stay filtered by their own authorities, so out-of-scope steps remain hidden
DROP POLICY IF EXISTS runs_api ON dq_ctl.runs;
CREATE POLICY runs_api ON dq_ctl.runs FOR SELECT TO dq_api
  USING ((cardinality(authorities) > 0 AND dq_ctl.unit_in_scope(authorities))
         OR EXISTS (SELECT 1 FROM dq_ctl.run_scopes s WHERE s.trace_id = runs.trace_id
                    AND dq_ctl.scope_overlaps(s.authorities)));
DROP POLICY IF EXISTS run_scopes_api ON dq_ctl.run_scopes;
CREATE POLICY run_scopes_api ON dq_ctl.run_scopes FOR SELECT TO dq_api USING (dq_ctl.scope_overlaps(authorities));
-- run events: a dq_api caller reads the events of a run it can read (the runs policy applies inside the subquery)
DROP POLICY IF EXISTS run_events_api ON dq_ctl.run_events;
CREATE POLICY run_events_api ON dq_ctl.run_events FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.runs r WHERE r.trace_id = run_events.trace_id));
-- explanation facts: only those of an explanation or LLM call the caller can read (facts are masked, still scoped)
DROP POLICY IF EXISTS explain_facts_api ON dq_ctl.explain_facts;
CREATE POLICY explain_facts_api ON dq_ctl.explain_facts FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.explanations e WHERE e.facts_hash = explain_facts.facts_hash)
         OR EXISTS (SELECT 1 FROM dq_ctl.llm_calls l WHERE l.facts_hash = explain_facts.facts_hash));
-- prompt versions are configuration, not data
DROP POLICY IF EXISTS prompt_versions_api ON dq_ctl.prompt_versions;
CREATE POLICY prompt_versions_api ON dq_ctl.prompt_versions FOR SELECT TO dq_api USING (true);
{sys_policies}

-- ===== grants: dq_trace_svc is the only writer; the api reads in scope; the auditor reads everything ==============
GRANT USAGE ON SCHEMA dq_ctl TO dq_trace_svc;
GRANT SELECT ON {tables} TO dq_api, dq_auditor, dq_trace_svc;
GRANT INSERT ON {tables} TO dq_trace_svc;
GRANT USAGE ON SEQUENCE dq_ctl.run_events_id_seq, dq_ctl.release_checks_id_seq, dq_ctl.llm_calls_call_id_seq,
  dq_ctl.guardrail_events_id_seq, dq_ctl.trace_id_seq TO dq_trace_svc;
REVOKE ALL ON SEQUENCE dq_ctl.trace_id_seq FROM PUBLIC, {others};
GRANT USAGE ON SEQUENCE dq_ctl.trace_id_seq TO dq_trace_svc;
-- F-34: the units a root touched (for the authority union at flush); read-only
GRANT SELECT ON dq_ctl.units TO dq_trace_svc;
DROP POLICY IF EXISTS units_trace ON dq_ctl.units;
CREATE POLICY units_trace ON dq_ctl.units FOR SELECT TO dq_trace_svc USING (true);

-- the tracer reads the high-water marks of a cycle (I9) and nothing else of the engine tables
GRANT SELECT ON dq_ctl.unit_events, dq_ctl.results, dq_ctl.manifest_versions, dq_ctl.feed_items TO dq_trace_svc;
DROP POLICY IF EXISTS unit_events_trace ON dq_ctl.unit_events;
CREATE POLICY unit_events_trace ON dq_ctl.unit_events FOR SELECT TO dq_trace_svc USING (true);
DROP POLICY IF EXISTS results_trace ON dq_ctl.results;
CREATE POLICY results_trace ON dq_ctl.results FOR SELECT TO dq_trace_svc USING (true);
DROP POLICY IF EXISTS feed_items_trace ON dq_ctl.feed_items;
CREATE POLICY feed_items_trace ON dq_ctl.feed_items FOR SELECT TO dq_trace_svc USING (true);
GRANT EXECUTE ON FUNCTION dq_ctl.counts_only(jsonb), dq_ctl.span_attrs_ok(jsonb), dq_ctl.span_refs_ok(text[]),
  dq_ctl.reasons_ok(text[]), dq_ctl.scope_overlaps(text[]) TO dq_trace_svc, dq_api, dq_auditor;

-- ===== correlation columns on the existing tables (outside every hash, never in a payload) =======================
DO $$BEGIN
  -- veto_window_status selects w.* followed by computed columns; once veto_windows gains columns the expansion
  -- changes, so the view is re-created (once; a re-apply then expands identically)
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'dq_ctl'
                 AND table_name = 'veto_window_status' AND column_name = 'trace_id') THEN
    DROP VIEW IF EXISTS dq_ctl.veto_window_status;
  END IF;
END$$;
{correlation}
{VETO_WINDOW_STATUS_VIEW}
{VETO_WINDOW_STATUS_GRANT}
-- F-34: from a unit or a decision to its runs (the Tower trace explorer). Invoker rights: each caller sees only the
-- events / decisions and the runs its own policies allow
CREATE OR REPLACE VIEW dq_ctl.unit_runs WITH (security_invoker = true) AS
  SELECT x.unit_key, x.trace_id FROM (
    SELECT e.unit_key, e.trace_id FROM dq_ctl.unit_events e WHERE e.trace_id IS NOT NULL
    UNION SELECT d.unit_key, d.trace_id FROM dq_ctl.decisions d WHERE d.trace_id IS NOT NULL
    UNION SELECT c.unit_key, s.trace_id FROM dq_ctl.release_checks c JOIN dq_ctl.spans s USING (span_id)) x
  WHERE EXISTS (SELECT 1 FROM dq_ctl.runs r WHERE r.trace_id = x.trace_id);
CREATE OR REPLACE VIEW dq_ctl.decision_runs WITH (security_invoker = true) AS
  SELECT d.decision_id, d.unit_key, d.trace_id FROM dq_ctl.decisions d
  WHERE d.trace_id IS NOT NULL AND EXISTS (SELECT 1 FROM dq_ctl.runs r WHERE r.trace_id = d.trace_id);
GRANT SELECT ON dq_ctl.unit_runs, dq_ctl.decision_runs TO dq_api, dq_auditor;
""" + evidence_ddl()


# ===== Part F R4-5 (DEC-023, DEC-024): evidence-gated promotion with a continuous track record =====================
# Single definition (R4-5 §1): eval_runs, the attestation function, eval_attestations_confirmed, evidence_complete,
# track_record_by_version and track_record live HERE, after eval_runs, and nowhere else (parte_ddl no longer defines
# the track record or the old 6-argument eval_attest).
EVAL_SUITES = ("agent_eval", "explainer")
EVAL_SOURCE_MODES = ("DETERMINISTIC", "LIVE", "SCRIPTED", "RECORDED", "TEMPLATE")
# R4-5 §4: the production minimums per TARGET tier (the demo policy carries smaller, labelled values). "min" counts
# are at least; the rest are at most. All are read on the CURRENT code version only.
DEFAULT_PROMOTION_MINIMUMS = {
    "L2": {"human_decisions": 10, "override_rate_max": 0.10, "denies": 0, "lookback_withdrawn": 0},
    "L3": {"human_releases": 20, "overrides": 0, "denies": 0, "lookback_withdrawn": 0},
    "L4": {"released_after_window": 30, "vetoes": 0, "denies": 0, "lookback_withdrawn": 0},
}
MINIMUM_AT_LEAST = {"human_decisions": "human decisions", "human_releases": "human-approved releases",
                    "released_after_window": "releases after a veto window"}
MINIMUM_AT_MOST = {"overrides": "overrides", "denies": "denies after release",
                   "lookback_withdrawn": "look-back withdrawals", "vetoes": "vetoes",
                   "override_rate_max": "override rate"}
# the counted event tables carry the code version of the session that wrote the event (CP.connect_as sets
# dq.code_sha256); unit_events and decisions already have it (CORRELATED), these two are added here
COUNTED_EXTRA = ("deny", "lookback_events")


def evidence_ddl() -> str:
    import json
    hex64 = "'^[0-9a-f]{64}$'"
    code_cols = "\n".join(
        f"ALTER TABLE dq_ctl.{t} ADD COLUMN IF NOT EXISTS code_sha256 text "
        f"DEFAULT nullif(current_setting('dq.code_sha256', true), '');" for t in COUNTED_EXTRA)
    defaults = json.dumps(DEFAULT_PROMOTION_MINIMUMS).replace("'", "''")
    return f"""
-- ===== R4-5: drop the earlier definitions (once; parte_ddl no longer creates them) =================================
DROP VIEW IF EXISTS dq_ctl.track_record;
DROP VIEW IF EXISTS dq_ctl.track_record_by_version;
DROP VIEW IF EXISTS dq_ctl.evidence_complete;
DROP VIEW IF EXISTS dq_ctl.eval_attestations_confirmed;
DROP FUNCTION IF EXISTS dq_ctl.eval_attest(text,text,int,int,text,text);
{code_cols}

-- ===== eval runs: what a suite run recorded (the recorder writes them; attestations only REFERENCE them) ==========
CREATE TABLE IF NOT EXISTS dq_ctl.eval_runs(
  run_id text PRIMARY KEY CHECK (run_id ~ '^[A-Za-z0-9_.:-]{{1,120}}$'),
  suite text NOT NULL CHECK (suite IN ({_l(EVAL_SUITES)})),
  source_mode text NOT NULL CHECK (source_mode IN ({_l(EVAL_SOURCE_MODES)})),
  cases int NOT NULL CHECK (cases >= 0), passed int NOT NULL CHECK (passed >= 0 AND passed <= cases),
  code_sha256 text NOT NULL CHECK (code_sha256 ~ {hex64}), prompt_version text,
  junit_sha256 text NOT NULL CHECK (junit_sha256 ~ {hex64}), at timestamptz NOT NULL DEFAULT clock_timestamp());

-- K11 / P2-1: eval_cases, explanation_feedback, eval_candidates, eval_candidate_events
CREATE TABLE IF NOT EXISTS dq_ctl.eval_cases(
  run_id text NOT NULL REFERENCES dq_ctl.eval_runs(run_id),
  case_id text NOT NULL,
  kind text NOT NULL,
  facts_hash text,
  expected jsonb,
  verdict text NOT NULL,
  reason_codes text[] DEFAULT '{{}}',
  passed boolean NOT NULL,
  PRIMARY KEY (run_id, case_id)
);

CREATE TABLE IF NOT EXISTS dq_ctl.explanation_feedback(
  id bigserial PRIMARY KEY,
  person_id text NOT NULL,
  facts_hash text NOT NULL,
  text_hash text NOT NULL,
  trace_id text,
  span_id text,
  verdict text NOT NULL CHECK (verdict IN ('ACCURATE','INACCURATE','UNCLEAR','UNSAFE')),
  comment text,
  at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS dq_ctl.eval_candidates(
  id bigserial PRIMARY KEY,
  feedback_id bigint REFERENCES dq_ctl.explanation_feedback(id),
  facts_hash text NOT NULL,
  verdict text NOT NULL,
  reason_code text,
  status text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED','PROMOTED','REJECTED')),
  cr_id text,
  at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS dq_ctl.eval_candidate_events(
  id bigserial PRIMARY KEY,
  candidate_id bigint NOT NULL REFERENCES dq_ctl.eval_candidates(id),
  event text NOT NULL,
  person_id text NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE OR REPLACE FUNCTION dq_ctl.eval_run_record(p_run_id text, p_suite text, p_source_mode text, p_cases int,
  p_passed int, p_code_sha256 text, p_prompt_version text, p_junit_sha256 text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF session_user <> 'dq_eval_svc' THEN
    RAISE EXCEPTION 'eval runs are recorded by the eval service' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO dq_ctl.eval_runs(run_id, suite, source_mode, cases, passed, code_sha256, prompt_version, junit_sha256)
    VALUES (p_run_id, p_suite, p_source_mode, p_cases, p_passed, p_code_sha256, p_prompt_version, p_junit_sha256);
  RETURN p_run_id;
END $$;

-- ===== evidence bundles: an attestation names a DETERMINISTIC run and, optionally, a LIVE explainer run ===========
ALTER TABLE dq_ctl.eval_attestations ADD COLUMN IF NOT EXISTS junit_sha256_llm text
  CHECK (junit_sha256_llm ~ {hex64});
ALTER TABLE dq_ctl.eval_attestations ADD COLUMN IF NOT EXISTS prompt_version text;
ALTER TABLE dq_ctl.eval_attestations ADD COLUMN IF NOT EXISTS det_run_id text;
ALTER TABLE dq_ctl.eval_attestations ADD COLUMN IF NOT EXISTS llm_run_id text;

CREATE OR REPLACE FUNCTION dq_ctl.eval_attest(p_label text, p_code_sha256 text, p_det_run_id text, p_llm_run_id text,
  p_person text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE d record; l record; i bigint; l_junit text; l_prompt text;
BEGIN
  IF session_user <> 'dq_api' OR p_person IS NULL THEN
    RAISE EXCEPTION 'eval attestations are recorded by a named person through the api' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT dq_ctl._governance_person(p_person) THEN
    RAISE EXCEPTION 'eval attestations are proposed by a governance person' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO d FROM dq_ctl.eval_runs WHERE run_id = p_det_run_id;
  IF NOT FOUND OR d.source_mode <> 'DETERMINISTIC' OR d.suite <> 'agent_eval' OR d.cases = 0 OR d.passed <> d.cases
     OR d.code_sha256 <> p_code_sha256 THEN
    RAISE EXCEPTION 'the deterministic half must be a fully passing DETERMINISTIC agent_eval run of code %',
      left(coalesce(p_code_sha256, '?'), 12) USING ERRCODE = 'check_violation';
  END IF;
  IF p_llm_run_id IS NOT NULL THEN
    SELECT * INTO l FROM dq_ctl.eval_runs WHERE run_id = p_llm_run_id;
    IF NOT FOUND OR l.source_mode <> 'LIVE' OR l.suite <> 'explainer' OR l.cases = 0 OR l.passed <> l.cases
       OR l.code_sha256 <> p_code_sha256 OR l.prompt_version IS NULL THEN
      RAISE EXCEPTION 'the LLM half must be a fully passing LIVE explainer run of code % with a recorded prompt version '
        '(a SCRIPTED, RECORDED or TEMPLATE run is not evidence)', left(p_code_sha256, 12) USING ERRCODE = 'check_violation';
    END IF;
    l_junit := l.junit_sha256; l_prompt := l.prompt_version;
  END IF;
  INSERT INTO dq_ctl.eval_attestations(label, code_sha256, tests, failures, junit_sha256, person_id, junit_sha256_llm,
                                       prompt_version, det_run_id, llm_run_id)
    VALUES (p_label, p_code_sha256, d.cases, d.cases - d.passed, d.junit_sha256, p_person,
            l_junit, l_prompt, p_det_run_id, p_llm_run_id)
    RETURNING id INTO i;
  PERFORM dq_ctl.append_audit('EVAL_ATTESTED', encode(sha256(convert_to(i || '|' || p_code_sha256 || '|' || p_person, 'UTF8')), 'hex'));
  RETURN i;
END $$;

CREATE OR REPLACE VIEW dq_ctl.eval_attestations_confirmed AS
  SELECT a.*, c.person_id AS confirmed_by, c.at AS confirmed_at
  FROM dq_ctl.eval_attestations a
  JOIN dq_ctl.eval_attestation_confirmations c ON c.attestation_id = a.id
  WHERE a.junit_sha256 IN (SELECT junit_sha256 FROM dq_ctl.eval_runs WHERE source_mode IN ('DETERMINISTIC','LIVE'));

-- complete for THIS deployment: test / demo need the deterministic half; pilot / production both halves. No binding
-- row reads as production (fail closed, R4-2).
CREATE OR REPLACE VIEW dq_ctl.evidence_complete AS
  WITH dep AS (SELECT coalesce((SELECT b.deployment FROM dq_ctl.deployment_binding b), 'production') AS d)
  SELECT a.code_sha256, a.prompt_version,
         (a.junit_sha256 IS NOT NULL AND (dep.d IN ('test','demo')
              OR (a.junit_sha256_llm IS NOT NULL AND a.prompt_version IS NOT NULL))) AS complete,
         a.id AS attestation_id
  FROM dq_ctl.eval_attestations_confirmed a CROSS JOIN dep;

-- ===== continuous track record: one row per (agent, action class, authority, code version); it never resets ======
-- _track_record_counts is the one counting definition (internal: services and the promotion check read it whatever
-- the session); track_record_by_version is the same rows under the api's scope.
DROP VIEW IF EXISTS dq_ctl.track_record_by_version;
CREATE OR REPLACE VIEW dq_ctl._track_record_counts AS
  WITH m AS (
    SELECT a AS authority, x.code_sha256, CASE
             WHEN x.to_state = 'RELEASED' AND x.from_state = 'EVALUATING' THEN
               CASE WHEN EXISTS (SELECT 1 FROM dq_ctl.veto_windows vw WHERE vw.unit_key = x.unit_key)
                    THEN 'released_after_window' ELSE 'autonomous' END
             WHEN x.to_state = 'RELEASED' AND x.from_state = 'PENDING_RELEASE' THEN 'human_released'
             WHEN x.to_state = 'HELD' AND x.via = 'VETO' AND x.from_state = 'EVALUATING' THEN 'vetoes' END AS metric,
           'e' || x.event_id AS k
      FROM dq_ctl.unit_events x JOIN dq_ctl.units u ON u.unit_key = x.unit_key CROSS JOIN unnest(u.authorities) a
    UNION ALL
    SELECT a, d.code_sha256, 'human_decisions', 'd' || d.decision_id
      FROM dq_ctl.decisions d JOIN dq_ctl.units u ON u.unit_key = d.unit_key CROSS JOIN unnest(u.authorities) a
    UNION ALL
    SELECT a, d.code_sha256, 'overrides', 'u' || d.unit_key
      FROM dq_ctl.decisions d JOIN dq_ctl.units u ON u.unit_key = d.unit_key CROSS JOIN unnest(u.authorities) a
     WHERE d.decision = 'RELEASE' AND d.disposition_vector::text ~ '"(FAIL|ERROR|UNBALANCEABLE)"'
    UNION ALL
    SELECT a, x.code_sha256, 'denies', 'u' || x.unit_key
      FROM dq_ctl.deny x JOIN dq_ctl.units u ON u.unit_key = x.unit_key CROSS JOIN unnest(u.authorities) a
     WHERE EXISTS (SELECT 1 FROM dq_ctl.unit_events r WHERE r.unit_key = x.unit_key AND r.to_state = 'RELEASED')
    UNION ALL
    SELECT li.authority, le.code_sha256, 'lookback_withdrawn', 'l' || le.id
      FROM dq_ctl.lookback_events le JOIN dq_ctl.lookback_items li USING (item_id) WHERE le.event = 'WITHDRAWN'),
  t AS (SELECT DISTINCT agent_id, action_class, authority FROM dq_ctl.autonomy_current
         WHERE action_class = 'RELEASE' AND agent_id = dq_ctl.seat_agent('dq_publish_svc'))
  SELECT t.agent_id, t.action_class, t.authority, m.code_sha256,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'autonomous') AS autonomous,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'released_after_window') AS released_after_window,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'human_released') AS human_released,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'human_decisions') AS human_decisions,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'vetoes') AS vetoes,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'overrides') AS overrides,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'denies') AS denies,
         count(DISTINCT m.k) FILTER (WHERE m.metric = 'lookback_withdrawn') AS lookback_withdrawn
  FROM t JOIN m ON m.authority = t.authority AND m.metric IS NOT NULL
  GROUP BY 1, 2, 3, 4;
CREATE OR REPLACE VIEW dq_ctl.track_record_by_version AS
  SELECT * FROM dq_ctl._track_record_counts WHERE session_user <> 'dq_api' OR dq_ctl.in_scope(authority);

-- the sum over ALL versions (never reset) plus the CURRENT version's block; prior_* = every earlier version's total
-- (kept for API compatibility). `since` is when the current tier began (shown, never used to reset a count).
CREATE OR REPLACE VIEW dq_ctl.track_record AS
  WITH t AS (SELECT agent_id, action_class, authority, min(coalesce(dq_ctl.tier_rank(tier), 1)) AS rank,
                    max(at) AS tier_at, (array_agg(cr_id ORDER BY at DESC))[1] AS cr_id
             FROM dq_ctl.autonomy_current GROUP BY 1, 2, 3),
       cur AS (SELECT nullif(current_setting('dq.code_sha256', true), '') AS v),
       ea AS (SELECT a.label, a.code_sha256, a.failures, a.tests, a.person_id, a.confirmed_by, a.confirmed_at,
                     a.junit_sha256_llm IS NOT NULL AS llm_half
              FROM dq_ctl.eval_attestations_confirmed a ORDER BY a.confirmed_at DESC, a.id DESC LIMIT 1)
  SELECT t.agent_id, t.action_class, t.authority, 'L' || t.rank AS tier, dq_ctl.tier_ceiling('L' || t.rank) AS ceiling_mode,
         t.cr_id, t.tier_at AS since,
         coalesce(sum(b.autonomous), 0)::bigint AS autonomous,
         coalesce(sum(b.released_after_window), 0)::bigint AS released_after_window,
         coalesce(sum(b.human_released), 0)::bigint AS human_released,
         coalesce(sum(b.human_decisions), 0)::bigint AS human_decisions,
         coalesce(sum(b.vetoes), 0)::bigint AS vetoes, coalesce(sum(b.overrides), 0)::bigint AS overrides,
         coalesce(sum(b.denies), 0)::bigint AS denies, coalesce(sum(b.lookback_withdrawn), 0)::bigint AS lookback_withdrawn,
         (SELECT ea.label FROM ea) AS eval_label, (SELECT ea.code_sha256 FROM ea) AS eval_code_sha256,
         (SELECT ea.tests FROM ea) AS eval_tests, (SELECT ea.failures FROM ea) AS eval_failures,
         NULL::timestamptz AS prior_since,
         coalesce(sum(b.autonomous) FILTER (WHERE b.code_sha256 IS DISTINCT FROM cur.v), 0)::bigint AS prior_autonomous,
         coalesce(sum(b.released_after_window) FILTER (WHERE b.code_sha256 IS DISTINCT FROM cur.v), 0)::bigint
           AS prior_released_after_window,
         coalesce(sum(b.vetoes) FILTER (WHERE b.code_sha256 IS DISTINCT FROM cur.v), 0)::bigint AS prior_vetoes,
         coalesce(sum(b.overrides) FILTER (WHERE b.code_sha256 IS DISTINCT FROM cur.v), 0)::bigint AS prior_overrides,
         coalesce(sum(b.denies) FILTER (WHERE b.code_sha256 IS DISTINCT FROM cur.v), 0)::bigint AS prior_denies,
         coalesce(sum(b.lookback_withdrawn) FILTER (WHERE b.code_sha256 IS DISTINCT FROM cur.v), 0)::bigint
           AS prior_lookback_withdrawn,
         (SELECT ea.person_id FROM ea) AS eval_proposed_by, (SELECT ea.confirmed_by FROM ea) AS eval_confirmed_by,
         (SELECT ea.confirmed_at FROM ea) AS eval_confirmed_at, (SELECT ea.llm_half FROM ea) AS eval_llm_half,
         cur.v AS current_code_sha256,
         jsonb_build_object('code_sha256', cur.v,
           'autonomous', coalesce(sum(b.autonomous) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'released_after_window', coalesce(sum(b.released_after_window) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'human_released', coalesce(sum(b.human_released) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'human_decisions', coalesce(sum(b.human_decisions) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'vetoes', coalesce(sum(b.vetoes) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'overrides', coalesce(sum(b.overrides) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'denies', coalesce(sum(b.denies) FILTER (WHERE b.code_sha256 = cur.v), 0),
           'lookback_withdrawn', coalesce(sum(b.lookback_withdrawn) FILTER (WHERE b.code_sha256 = cur.v), 0))
           AS current_version
  FROM t CROSS JOIN cur
  LEFT JOIN dq_ctl._track_record_counts b
    ON b.agent_id = t.agent_id AND b.action_class = t.action_class AND b.authority = t.authority
  WHERE session_user <> 'dq_api' OR dq_ctl.in_scope(t.authority)
  GROUP BY t.agent_id, t.action_class, t.authority, t.rank, t.tier_at, t.cr_id, cur.v;

-- ===== the governed minimums (POLICY promotion_minimums; production defaults when the key is absent) =============
CREATE OR REPLACE FUNCTION dq_ctl.promotion_minimums(p_state text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT coalesce((SELECT c.content->'promotion_minimums' FROM dq_ctl.config_effective c
                    WHERE c.artifact_id = p_state || ':policy' AND c.op = 'PUT'), '{defaults}'::jsonb) $$;

CREATE OR REPLACE FUNCTION dq_ctl.evidence_status() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  WITH s AS (SELECT nullif(current_setting('dq.code_sha256', true), '') AS code,
                    nullif(current_setting('dq.prompt_version', true), '') AS pv,
                    coalesce((SELECT b.deployment FROM dq_ctl.deployment_binding b), 'production') AS dep)
  SELECT jsonb_build_object('code_sha256', s.code, 'prompt_version', s.pv, 'deployment', s.dep,
    'deterministic', EXISTS (SELECT 1 FROM dq_ctl.eval_attestations_confirmed a WHERE a.code_sha256 = s.code),
    'llm', EXISTS (SELECT 1 FROM dq_ctl.eval_attestations_confirmed a WHERE a.code_sha256 = s.code
                   AND a.junit_sha256_llm IS NOT NULL AND a.prompt_version = s.pv),
    'llm_required', s.dep NOT IN ('test','demo'),
    'complete', EXISTS (SELECT 1 FROM dq_ctl.evidence_complete e WHERE e.code_sha256 = s.code AND e.complete
                          AND (s.dep IN ('test','demo') OR e.prompt_version = s.pv)))
  FROM s $$;

-- NULL when every AUTONOMY rank rise in p_items has complete evidence for the RUNNING code version and a current-
-- version record meeting the target tier's minimums; else the first refusal. Lowerings need neither (demotion is
-- automatic and evidence-free). p_origin = 'GENESIS' exempts ONLY this call's rank rises from the evidence/minimum-
-- record check: a genesis seed grant is a one-time ratification of the state's starting configuration, not an
-- operational promotion backed by a track record that cannot exist before genesis has happened. It still goes
-- through gov_decide/gov_apply's other checks (two-person approval, author != approver, etc.) unchanged. A LATER,
-- non-GENESIS change request promoting the same agent/authority is never exempted: p_origin is per-change-request,
-- re-checked fresh on every call, and is never persisted onto the authority or carried forward.
DROP FUNCTION IF EXISTS dq_ctl.promotion_refusal(text,jsonb);
CREATE OR REPLACE FUNCTION dq_ctl.promotion_refusal(p_state text, p_items jsonb, p_origin text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE it jsonb; old jsonb; ag text; ac text; t record; ev jsonb; mins jsonb; m jsonb; rec record; k text;
        need numeric; have numeric; lbl text;
BEGIN
  IF p_origin = 'GENESIS' THEN RETURN NULL; END IF;
  FOR it IN SELECT * FROM jsonb_array_elements(p_items) LOOP
    CONTINUE WHEN it->>'kind' <> 'AUTONOMY' OR it->>'op' <> 'PUT';
    SELECT c.content INTO old FROM dq_ctl.config_effective c WHERE c.artifact_id = it->>'artifact_id' AND c.op = 'PUT';
    ag := split_part(it->>'artifact_id', ':', 3); ac := split_part(it->>'artifact_id', ':', 4);
    FOR t IN SELECT e.key AS authority, e.value AS tier FROM jsonb_each_text(it->'content') e
             WHERE coalesce(dq_ctl.tier_rank(e.value), 0) > coalesce(dq_ctl.tier_rank(old->>e.key), 1) LOOP
      ev := dq_ctl.evidence_status();
      IF NOT (ev->>'complete')::boolean THEN
        RETURN format('promotion requires complete evidence for code %s: deterministic %s / live LLM %s',
                      coalesce(left(ev->>'code_sha256', 12), '<unknown>'),
                      CASE WHEN (ev->>'deterministic')::boolean THEN '✓' ELSE '✗' END,
                      CASE WHEN (ev->>'llm')::boolean THEN '✓' WHEN (ev->>'llm_required')::boolean THEN '✗'
                           ELSE '✗ (not required in ' || (ev->>'deployment') || ')' END);
      END IF;
      mins := dq_ctl.promotion_minimums(p_state);
      m := coalesce(mins->t.tier, '{defaults}'::jsonb->t.tier, '{{}}'::jsonb);
      SELECT coalesce(sum(b.human_decisions), 0) AS human_decisions, coalesce(sum(b.human_released), 0) AS human_releases,
             coalesce(sum(b.released_after_window), 0) AS released_after_window, coalesce(sum(b.overrides), 0) AS overrides,
             coalesce(sum(b.denies), 0) AS denies, coalesce(sum(b.lookback_withdrawn), 0) AS lookback_withdrawn,
             coalesce(sum(b.vetoes), 0) AS vetoes INTO rec
        FROM dq_ctl._track_record_counts b
       WHERE b.agent_id = ag AND b.action_class = ac AND b.authority = t.authority
         AND b.code_sha256 = ev->>'code_sha256';
      FOR k IN SELECT jsonb_object_keys(m) LOOP
        need := (m->>k)::numeric;
        IF k = 'override_rate_max' THEN
          have := CASE WHEN rec.human_decisions = 0 THEN 0 ELSE rec.overrides::numeric / rec.human_decisions END;
          IF have > need THEN
            RETURN format('promotion requires a minimum record: override rate %s%% (at most %s%%) on %s',
                          round(have * 100, 1), round(need * 100, 1), t.authority);
          END IF;
        ELSIF k IN ('human_decisions','human_releases','released_after_window') THEN
          have := CASE k WHEN 'human_decisions' THEN rec.human_decisions WHEN 'human_releases' THEN rec.human_releases
                         ELSE rec.released_after_window END;
          lbl := CASE k WHEN 'human_decisions' THEN 'human decisions' WHEN 'human_releases' THEN 'human-approved releases'
                        ELSE 'releases after a veto window' END;
          IF have < need THEN
            RETURN format('promotion requires a minimum record: %s of %s %s', have, need, lbl);
          END IF;
        ELSIF k IN ('overrides','denies','lookback_withdrawn','vetoes') THEN
          have := CASE k WHEN 'overrides' THEN rec.overrides WHEN 'denies' THEN rec.denies
                         WHEN 'lookback_withdrawn' THEN rec.lookback_withdrawn ELSE rec.vetoes END;
          lbl := CASE k WHEN 'overrides' THEN 'overrides' WHEN 'denies' THEN 'denies after release'
                        WHEN 'lookback_withdrawn' THEN 'look-back withdrawals' ELSE 'vetoes' END;
          IF have > need THEN
            RETURN format('promotion requires a minimum record: %s %s (at most %s)', have, lbl, need);
          END IF;
        END IF;                                       -- other keys (a label) carry no requirement
      END LOOP;
    END LOOP;
  END LOOP;
  RETURN NULL;
END $$;

-- ===== R4-5 grants (the read grants the old parte_ddl definitions carried, plus the new objects) ===============
DO $$BEGIN
  EXECUTE 'DROP TRIGGER IF EXISTS eval_runs_no_mut ON dq_ctl.eval_runs';
  EXECUTE 'CREATE TRIGGER eval_runs_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.eval_runs FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation()';
  EXECUTE 'DROP TRIGGER IF EXISTS eval_runs_no_trunc ON dq_ctl.eval_runs';
  EXECUTE 'CREATE TRIGGER eval_runs_no_trunc BEFORE TRUNCATE ON dq_ctl.eval_runs FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation()';
END$$;
REVOKE ALL ON dq_ctl.eval_runs FROM PUBLIC;
REVOKE ALL ON dq_ctl._track_record_counts FROM PUBLIC;
GRANT SELECT, INSERT ON dq_ctl.eval_runs TO dq_eval_svc, dq_api, dq_auditor;
GRANT SELECT, INSERT ON dq_ctl.eval_cases TO dq_eval_svc, dq_api, dq_auditor;
GRANT SELECT, INSERT ON dq_ctl.explanation_feedback TO dq_api, dq_auditor;
GRANT SELECT, INSERT, UPDATE ON dq_ctl.eval_candidates TO dq_api, dq_registry_svc, dq_auditor;
GRANT SELECT, INSERT ON dq_ctl.eval_candidate_events TO dq_api, dq_registry_svc, dq_auditor;
GRANT USAGE, SELECT ON SEQUENCE dq_ctl.explanation_feedback_id_seq, dq_ctl.eval_candidates_id_seq, dq_ctl.eval_candidate_events_id_seq TO dq_api, dq_registry_svc, dq_eval_svc, dq_auditor;
REVOKE ALL ON SEQUENCE dq_ctl.trace_id_seq FROM PUBLIC, dq_api, dq_registry_svc, dq_eval_svc, dq_auditor, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_triage_svc, dq_monitor_svc;
GRANT USAGE, SELECT ON SEQUENCE dq_ctl.trace_id_seq TO dq_trace_svc;
GRANT SELECT ON dq_ctl.eval_attestations_confirmed, dq_ctl.evidence_complete
  TO dq_api, dq_registry_svc, dq_publish_svc, dq_eval_svc, dq_auditor;
GRANT SELECT ON dq_ctl.track_record, dq_ctl.track_record_by_version
  TO dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_triage_svc, dq_monitor_svc, dq_registry_svc, dq_auditor;
REVOKE ALL ON FUNCTION dq_ctl.eval_run_record(text,text,text,int,int,text,text,text),
  dq_ctl.eval_attest(text,text,text,text,text), dq_ctl.promotion_minimums(text), dq_ctl.evidence_status(),
  dq_ctl.promotion_refusal(text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.eval_run_record(text,text,text,int,int,text,text,text) TO dq_eval_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.eval_attest(text,text,text,text,text) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.promotion_minimums(text), dq_ctl.evidence_status(), dq_ctl.promotion_refusal(text,jsonb,text)
  TO dq_api, dq_registry_svc, dq_auditor;
""".replace("{defaults}", defaults)
