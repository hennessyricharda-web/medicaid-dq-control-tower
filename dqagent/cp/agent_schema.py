"""Agent state, blackboard and memory in the control plane (Part C WS3; agent namespace Part E §8 B1).

  dq_ctl.agent_blackboard   append-only, hash-chained shared surface the agents' seats write and humans read.
                            The ONLY write path is dq_ctl.bb_post(), a SECURITY DEFINER function that derives the
                            AGENT and the SEAT from session_user (the service login) through SEATS, so a caller can
                            choose neither. A steward (human) post comes through the api role and MUST carry the person
                            id taken from a verified token. Bodies are structured (whitelisted keys; string values are
                            short identifiers only), so no data values or free text (e.g. diagnosis codes, 42 CFR
                            Part 2) can enter the board: prose is rendered from `template` + body by the reader.
                            Part E: every entry carries `agent` (inside the hash), refs are TYPED text ids
                            (bb:<seq>, mem:<id>, rule:<id>@<entity>, unit:<key>, cr:<id>, lookback:<id>, feed:<id>,
                            pr:<id>), and an AGENT seat posts only while its agent is ACTIVE, the seat is bound to it
                            and it holds a live POST_BOARD entitlement for the authority (fail closed).
  dq_ctl.agent_memory       durable agent memory. BASELINE (system, advisory, pinned by hash), SPC_BASELINE (the
                            MONITOR seat's control limits; frozen once pinned, re-baselined only through an applied
                            MONITOR change request) and KNOWN_ISSUE (human; PROPOSED by one person, APPROVED by a
                            DIFFERENT person with a step-up; mandatory expiry; annotates the maker's card only and
                            never changes a disposition). Pinning needs a live PIN_MEMORY entitlement.
  dq_ctl.agent_state        security-invoker view: per batch / node, units by state, open holds, board entries.

Seats are of two sorts. AGENT seats (INTAKE, EVALUATOR, TRIAGE, PUBLISHER, VERIFIER, MONITOR) act for an agent and
are subject to its registry status and entitlements. CONTROL seats (STEWARD = a person through the api, REGISTRY =
the governance service) are not the agent acting; they may post into a registered agent's namespace (e.g. a
look-back flag or a demotion notice about that agent) and are never refused because the agent lost an entitlement.

T1 honesty note (red-team F2): seat attribution rests on each service logging in as its own role over local
trust auth. Any local process that can open that socket can log in as a service. Production replaces this with
a workload identity per service (mTLS / IAM-issued DB credentials); the function-level seat derivation is
unchanged.
"""

DQ_AGENT = "dq-reconciliation"

# role -> (agent, seat). The database derives both from the login; nothing a caller passes can change them.
SEATS = {"dq_seal_svc": (DQ_AGENT, "INTAKE"), "dq_eval_svc": (DQ_AGENT, "EVALUATOR"),
         "dq_triage_svc": (DQ_AGENT, "TRIAGE"), "dq_registry_svc": (DQ_AGENT, "REGISTRY"),
         "dq_publish_svc": (DQ_AGENT, "PUBLISHER"), "dq_verify_svc": (DQ_AGENT, "VERIFIER"),
         "dq_monitor_svc": (DQ_AGENT, "MONITOR"), "dq_api": (DQ_AGENT, "STEWARD")}
CONTROL_SEATS = ("STEWARD", "REGISTRY")
SEAT_OF = {r: s for r, (_, s) in SEATS.items()}

REF_KINDS = ("bb", "mem", "rule", "unit", "cr", "lookback", "feed", "pr", "trace", "element")
REF_PATTERN = (r"^(bb:[0-9]{1,18}|mem:[0-9]{1,18}|rule:[A-Za-z0-9_.-]{1,64}@[A-Za-z0-9_.-]{1,64}"
               r"|unit:[A-Za-z0-9_.:@|/+=-]{1,200}|cr:[A-Za-z0-9_-]{1,64}|lookback:[0-9]{1,18}|feed:[0-9]{1,18}"
               r"|pr:[0-9]{1,18}|trace:[A-Za-z0-9_.:-]{1,128}"
               r"|element:[A-Za-z0-9_.-]{1,64})$")              # element: a CDE citation (validated only)      # trace: Part F P1-1 (validated only)

BODY_KEYS = ["rule_id", "rule_ids", "unit_count", "unit_keys", "submitter_id", "batch_id", "node", "measure_id",
             "value", "threshold", "disposition", "state", "hop", "count", "counts", "reason_code", "memory_id",
             "decision_id", "program", "periods", "median", "n_periods", "delta", "open_units", "released",
             "held", "pending", "authority", "expires", "files", "template_args", "cr_id", "rule_hash",
             # Part E: autonomy, feeds, monitoring (identifiers and numbers only, like every other key)
             "agent_id", "action_class", "from_tier", "to_tier", "tier", "ref", "feed", "item_id", "period",
             "relation", "metric", "key", "rule", "cl", "ucl", "lcl", "mr_bar", "p_bar", "sigma", "psi", "n",
             "min_periods", "shares", "baseline_ref",
             # K8 (E-09, BACKLOG:26, ADJ-6): the SPC-baseline-pin path's own frozen document, today carried under
             # the generic `template_args` key (`runtime.py:_pin_baselines`) only because this list had no
             # purpose-named key for it. Widening the allow-list only (no key is removed, no write path changed
             # by this one-line addition) -- the ADJ-6 before/after determinism gate
             # (`tests/t1/test_agent_eval.py::test_agent_scenario_matches_recipe_and_is_deterministic`) was run
             # both before and after this change, confirmed executing for real against Postgres both times, with
             # matching digests (11/11 scenarios, before and after).
             "baseline"]


def _case(idx: int) -> str:
    return " ".join(f"WHEN '{r}' THEN '{v[idx]}'" for r, v in SEATS.items())


def agent_ddl() -> str:
    agent_case, seat_case = _case(0), _case(1)
    control = ",".join(f"'{s}'" for s in CONTROL_SEATS)
    keys = ",".join(f"'{k}'" for k in BODY_KEYS)
    return f"""
-- a pre-Part-E blackboard has no agent namespace and integer refs; its hash chain cannot be re-keyed in place
DO $$BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='dq_ctl' AND table_name='agent_blackboard')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='dq_ctl'
                     AND table_name='agent_blackboard' AND column_name='agent') THEN
    RAISE EXCEPTION 'pre-Part-E agent_blackboard found: archive it and start a new chain (the hash format changed)';
  END IF;
END$$;

-- typed references (Part E B1): bb:<seq> mem:<id> rule:<id>@<entity> unit:<key> cr:<id> lookback:<id> feed:<id> pr:<id>
CREATE OR REPLACE FUNCTION dq_ctl.refs_ok(p_refs text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(cardinality(p_refs), 0) <= 32
     AND coalesce((SELECT bool_and(r ~ '{REF_PATTERN}') FROM unnest(p_refs) r), true)
$$;

-- ===== agent blackboard =====================================================================
CREATE TABLE IF NOT EXISTS dq_ctl.agent_blackboard(
  seq bigint PRIMARY KEY, agent text NOT NULL CHECK (agent ~ '^[a-z0-9-]{{1,48}}$'),
  batch_id text, authority text NOT NULL, seat text NOT NULL, person_id text,
  kind text NOT NULL CHECK (kind IN ('FINDING','HYPOTHESIS','DECISION_REF','NOTE','MEMORY_REF')),
  subject_type text NOT NULL CHECK (subject_type IN ('BATCH','UNIT','RULE','FEED','SUBMITTER','MEASURE','AGENT',
                                                     'METRIC','PARALLEL_RUN')),
  subject_id text NOT NULL, template text NOT NULL CHECK (template ~ '^[A-Z0-9_]{{1,48}}$'),
  body jsonb NOT NULL, refs text[] NOT NULL DEFAULT '{{}}' CHECK (dq_ctl.refs_ok(refs)),
  prev_hash text NOT NULL, entry_hash text NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((seat = 'STEWARD') = (person_id IS NOT NULL)));
CREATE TABLE IF NOT EXISTS dq_ctl.agent_blackboard_head(
  id int PRIMARY KEY CHECK (id = 1), seq bigint NOT NULL, head_hash text NOT NULL);
INSERT INTO dq_ctl.agent_blackboard_head VALUES (1, 0, repeat('0',64)) ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION dq_ctl.agent_body_ok(p_body jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT jsonb_typeof(p_body) = 'object'
     AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN ({keys}))
     AND coalesce((SELECT bool_and((v #>> '{{}}') ~ '^[A-Za-z0-9_.:@|/+>=-]{{0,96}}$')
                   FROM jsonb_path_query(p_body, 'strict $.**') v WHERE jsonb_typeof(v) = 'string'), true)
$$;

-- the role -> (agent, seat) map, generated from agent_schema.SEATS (single source)
CREATE OR REPLACE FUNCTION dq_ctl.seat_agent(p_role text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$ SELECT CASE p_role {agent_case} ELSE NULL END $$;
CREATE OR REPLACE FUNCTION dq_ctl.seat_name(p_role text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$ SELECT CASE p_role {seat_case} ELSE NULL END $$;

DROP FUNCTION IF EXISTS dq_ctl.bb_hash(text,bigint,text,text,text,text,text,text,text,text,jsonb,bigint[]);
CREATE OR REPLACE FUNCTION dq_ctl.bb_hash(p_prev text, p_seq bigint, p_agent text, p_batch text, p_auth text,
  p_seat text, p_person text, p_kind text, p_st text, p_sid text, p_tpl text, p_body jsonb, p_refs text[])
RETURNS text LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT encode(sha256(convert_to(concat_ws('|', p_prev, p_seq::text, p_agent, p_batch, p_auth, p_seat, p_person,
         p_kind, p_st, p_sid, p_tpl, p_body::text, array_to_string(p_refs, ',')), 'UTF8')), 'hex')
$$;

-- recomputes the whole chain: returns the first broken seq, or NULL when every entry links and hashes correctly
CREATE OR REPLACE FUNCTION dq_ctl.bb_verify() RETURNS bigint
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE r record; prev text := repeat('0',64);
BEGIN
  FOR r IN SELECT * FROM dq_ctl.agent_blackboard ORDER BY seq LOOP
    IF r.prev_hash <> prev OR r.entry_hash <> dq_ctl.bb_hash(r.prev_hash, r.seq, r.agent, coalesce(r.batch_id,''),
         r.authority, r.seat, coalesce(r.person_id,''), r.kind, r.subject_type, r.subject_id, r.template, r.body,
         r.refs) THEN
      RETURN r.seq;
    END IF;
    prev := r.entry_hash;
  END LOOP;
  RETURN NULL;
END $$;

-- INTERNAL (owner only): validate the structure and append one entry to the chain. Callers derive agent and seat.
CREATE OR REPLACE FUNCTION dq_ctl._bb_append(p_agent text, p_seat text, p_person text, p_batch text, p_authority text,
  p_kind text, p_subject_type text, p_subject_id text, p_template text, p_body jsonb, p_refs text[])
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE h record; ns bigint; nh text;
BEGIN
  IF NOT dq_ctl.agent_body_ok(p_body) OR NOT (p_subject_id ~ '^[A-Za-z0-9_.:@|/+>=-]{{1,160}}$') THEN
    RAISE EXCEPTION 'blackboard body / subject is not structured (whitelisted keys, identifier values only)' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT dq_ctl.refs_ok(coalesce(p_refs, '{{}}')) THEN
    RAISE EXCEPTION 'blackboard refs must be typed ids (bb: mem: rule: unit: cr: lookback: feed: pr:)' USING ERRCODE = 'check_violation';
  END IF;
  SELECT * INTO h FROM dq_ctl.agent_blackboard_head WHERE id = 1 FOR UPDATE;
  ns := h.seq + 1;
  nh := dq_ctl.bb_hash(h.head_hash, ns, p_agent, coalesce(p_batch,''), p_authority, p_seat, coalesce(p_person,''),
        p_kind, p_subject_type, p_subject_id, p_template, p_body, coalesce(p_refs, '{{}}'));
  INSERT INTO dq_ctl.agent_blackboard(seq,agent,batch_id,authority,seat,person_id,kind,subject_type,subject_id,template,
                                      body,refs,prev_hash,entry_hash)
    VALUES (ns, p_agent, p_batch, p_authority, p_seat, p_person, p_kind, p_subject_type, p_subject_id, p_template,
            p_body, coalesce(p_refs, '{{}}'), h.head_hash, nh);
  UPDATE dq_ctl.agent_blackboard_head SET seq = ns, head_hash = nh WHERE id = 1;
  RETURN ns;
END $$;

DROP FUNCTION IF EXISTS dq_ctl.bb_post(text,text,text,text,text,text,jsonb,bigint[],text);
CREATE OR REPLACE FUNCTION dq_ctl.bb_post(p_batch text, p_authority text, p_kind text, p_subject_type text,
  p_subject_id text, p_template text, p_body jsonb, p_refs text[], p_person text, p_agent text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE s text; a text; why text;
BEGIN
  s := dq_ctl.seat_name(session_user);       -- the SEAT and the AGENT are the login, never an argument
  a := dq_ctl.seat_agent(session_user);
  IF s IS NULL THEN RAISE EXCEPTION 'role % holds no blackboard seat', session_user USING ERRCODE = 'insufficient_privilege'; END IF;
  IF (s = 'STEWARD') <> (p_person IS NOT NULL) THEN
    RAISE EXCEPTION 'a STEWARD post carries the verified person id; a service post carries none' USING ERRCODE = 'check_violation';
  END IF;
  IF s = 'STEWARD' AND NOT dq_ctl.in_scope(p_authority) THEN
    RAISE EXCEPTION 'authority % not in the steward''s scope', p_authority USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF s IN ({control}) THEN                    -- a person / the governance service writing ABOUT a registered agent
    a := coalesce(p_agent, a);
    IF NOT EXISTS (SELECT 1 FROM dq_ctl.agents_current g WHERE g.agent_id = a) THEN
      RAISE EXCEPTION 'agent % is not registered', a USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSE                                        -- the agent acting through one of its seats
    IF p_agent IS NOT NULL AND p_agent <> a THEN
      RAISE EXCEPTION 'seat % posts only in the namespace of agent %', s, a USING ERRCODE = 'insufficient_privilege';
    END IF;
    why := dq_ctl.agent_refusal(a, s, 'POST_BOARD', p_authority);
    IF why IS NOT NULL THEN RAISE EXCEPTION 'blackboard post refused: %', why USING ERRCODE = 'insufficient_privilege'; END IF;
  END IF;
  RETURN dq_ctl._bb_append(a, s, p_person, p_batch, p_authority, p_kind, p_subject_type, p_subject_id, p_template,
                           p_body, p_refs);
END $$;

-- ===== agent memory ========================================================================
CREATE TABLE IF NOT EXISTS dq_ctl.agent_memory(
  memory_id bigserial PRIMARY KEY, agent text NOT NULL CHECK (agent ~ '^[a-z0-9-]{{1,48}}$'),
  kind text NOT NULL CHECK (kind IN ('BASELINE','KNOWN_ISSUE','SPC_BASELINE')),
  scope_type text NOT NULL CHECK (scope_type IN ('RULE','MEASURE','SUBMITTER','FEED','METRIC')),
  scope_id text NOT NULL, authority text NOT NULL, content jsonb NOT NULL, content_hash text NOT NULL,
  created_seat text NOT NULL, proposed_by text, expires_at timestamptz,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (kind <> 'KNOWN_ISSUE' OR (proposed_by IS NOT NULL AND expires_at IS NOT NULL)),
  CHECK (kind <> 'SPC_BASELINE' OR scope_type = 'METRIC'),
  CHECK (dq_ctl.agent_body_ok(content)));
CREATE TABLE IF NOT EXISTS dq_ctl.agent_memory_events(
  id bigserial PRIMARY KEY, memory_id bigint NOT NULL REFERENCES dq_ctl.agent_memory(memory_id),
  event text NOT NULL CHECK (event IN ('PROPOSED','APPROVED','REJECTED','PINNED','SUPERSEDED')),
  person_id text, jti text UNIQUE, at timestamptz NOT NULL DEFAULT clock_timestamp());

DROP FUNCTION IF EXISTS dq_ctl.mem_pin_baseline(text,text,text,jsonb);
CREATE OR REPLACE FUNCTION dq_ctl.mem_pin_baseline(p_scope_type text, p_scope_id text, p_authority text, p_content jsonb,
  p_kind text DEFAULT 'BASELINE')
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE s text; a text; why text; mid bigint; hsh text; prev record;
BEGIN
  s := dq_ctl.seat_name(session_user);
  a := dq_ctl.seat_agent(session_user);
  IF p_kind = 'BASELINE' AND s IS DISTINCT FROM 'TRIAGE' AND s IS DISTINCT FROM 'PUBLISHER' THEN
    RAISE EXCEPTION 'only the triage / publisher seats pin baselines' USING ERRCODE = 'insufficient_privilege';
  ELSIF p_kind = 'SPC_BASELINE' AND s IS DISTINCT FROM 'MONITOR' THEN
    RAISE EXCEPTION 'only the monitor seat pins control-chart baselines' USING ERRCODE = 'insufficient_privilege';
  ELSIF p_kind NOT IN ('BASELINE','SPC_BASELINE') THEN
    RAISE EXCEPTION 'pinned memory is BASELINE | SPC_BASELINE' USING ERRCODE = 'check_violation';
  END IF;
  why := dq_ctl.agent_refusal(a, s, 'PIN_MEMORY', p_authority);
  IF why IS NOT NULL THEN RAISE EXCEPTION 'memory pin refused: %', why USING ERRCODE = 'insufficient_privilege'; END IF;
  hsh := encode(sha256(convert_to(p_content::text, 'UTF8')), 'hex');
  SELECT m.memory_id, m.content_hash, m.at INTO prev FROM dq_ctl.agent_memory m
   WHERE m.kind = p_kind AND m.agent = a AND m.scope_type = p_scope_type AND m.scope_id = p_scope_id
   ORDER BY m.memory_id DESC LIMIT 1;
  IF prev.memory_id IS NOT NULL AND prev.content_hash = hsh THEN RETURN prev.memory_id; END IF;
  -- a control-chart baseline is FROZEN once pinned (no drift into a poisoned baseline); it is replaced only after
  -- an applied MONITOR change request lists the series under `rebaseline`
  IF p_kind = 'SPC_BASELINE' AND prev.memory_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM dq_ctl.config_effective c WHERE c.kind = 'MONITOR' AND c.op = 'PUT'
          AND coalesce(c.content->'rebaseline', '[]'::jsonb) ? p_scope_id AND c.at > prev.at) THEN
    RAISE EXCEPTION 'control-chart baseline % is frozen; re-baselining is a MONITOR change request', p_scope_id
      USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.agent_memory(agent,kind,scope_type,scope_id,authority,content,content_hash,created_seat)
    VALUES (a,p_kind,p_scope_type,p_scope_id,p_authority,p_content,hsh,s) RETURNING memory_id INTO mid;
  INSERT INTO dq_ctl.agent_memory_events(memory_id,event) VALUES (mid,'PINNED');
  IF prev.memory_id IS NOT NULL THEN INSERT INTO dq_ctl.agent_memory_events(memory_id,event) VALUES (prev.memory_id,'SUPERSEDED'); END IF;
  RETURN mid;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.mem_propose_known_issue(p_scope_type text, p_scope_id text, p_authority text,
  p_content jsonb, p_person text, p_expires timestamptz)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE mid bigint;
BEGIN
  IF session_user <> 'dq_api' OR p_person IS NULL THEN RAISE EXCEPTION 'known issues are proposed by a person through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NOT dq_ctl.in_scope(p_authority) THEN RAISE EXCEPTION 'authority not in scope' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF p_expires IS NULL OR p_expires > clock_timestamp() + interval '90 days' OR p_expires <= clock_timestamp() THEN
    RAISE EXCEPTION 'a known issue needs an expiry within 90 days' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.agent_memory(agent,kind,scope_type,scope_id,authority,content,content_hash,created_seat,proposed_by,expires_at)
    VALUES (dq_ctl.seat_agent(session_user),'KNOWN_ISSUE',p_scope_type,p_scope_id,p_authority,p_content,
            encode(sha256(convert_to(p_content::text,'UTF8')),'hex'),'STEWARD',p_person,p_expires)
    RETURNING memory_id INTO mid;
  INSERT INTO dq_ctl.agent_memory_events(memory_id,event,person_id) VALUES (mid,'PROPOSED',p_person);
  RETURN mid;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.mem_decide_known_issue(p_memory bigint, p_person text, p_jti text, p_verdict text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE m record;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'decided through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  SELECT * INTO m FROM dq_ctl.agent_memory WHERE memory_id = p_memory AND kind = 'KNOWN_ISSUE';
  IF NOT FOUND OR NOT dq_ctl.in_scope(m.authority) THEN RAISE EXCEPTION 'unknown known-issue' USING ERRCODE = 'no_data_found'; END IF;
  IF m.proposed_by = p_person THEN RAISE EXCEPTION 'two-person rule: the proposer cannot approve' USING ERRCODE = 'check_violation'; END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.agent_memory_events WHERE memory_id = p_memory AND event IN ('APPROVED','REJECTED')) THEN
    RAISE EXCEPTION 'already decided' USING ERRCODE = 'unique_violation';
  END IF;
  IF p_verdict NOT IN ('APPROVED','REJECTED') OR p_jti IS NULL THEN RAISE EXCEPTION 'bad verdict' USING ERRCODE = 'check_violation'; END IF;
  INSERT INTO dq_ctl.agent_memory_events(memory_id,event,person_id,jti) VALUES (p_memory,p_verdict,p_person,p_jti);
END $$;

CREATE OR REPLACE VIEW dq_ctl.agent_memory_current WITH (security_invoker = true) AS
  SELECT m.*, (SELECT e.event FROM dq_ctl.agent_memory_events e WHERE e.memory_id = m.memory_id
               ORDER BY e.id DESC LIMIT 1) AS status,
         (SELECT e.person_id FROM dq_ctl.agent_memory_events e WHERE e.memory_id = m.memory_id
            AND e.event = 'APPROVED' LIMIT 1) AS approved_by
  FROM dq_ctl.agent_memory m;

-- ===== agent state =========================================================================
CREATE OR REPLACE VIEW dq_ctl.agent_state WITH (security_invoker = true) AS
  SELECT u.batch_id, u.node, count(*) AS units,
         count(*) FILTER (WHERE u.state = 'RELEASED') AS released,
         count(*) FILTER (WHERE u.state = 'HELD') AS held,
         count(*) FILTER (WHERE u.state = 'PENDING_DEPENDENCY') AS pending,
         count(*) FILTER (WHERE u.state NOT IN ('RELEASED','REJECTED','SUPERSEDED','HELD','PENDING_DEPENDENCY')) AS in_flight,
         (SELECT count(*) FROM dq_ctl.agent_blackboard b WHERE b.batch_id = u.batch_id) AS board_entries,
         (SELECT count(*) FROM dq_ctl.agent_blackboard b WHERE b.batch_id = u.batch_id AND b.kind = 'HYPOTHESIS') AS hypotheses
  FROM dq_ctl.units u GROUP BY u.batch_id, u.node;

-- ===== append-only, RLS, grants ============================================================
DROP TRIGGER IF EXISTS agent_blackboard_no_mut ON dq_ctl.agent_blackboard;
CREATE TRIGGER agent_blackboard_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.agent_blackboard FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS agent_memory_no_mut ON dq_ctl.agent_memory;
CREATE TRIGGER agent_memory_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.agent_memory FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS agent_memory_events_no_mut ON dq_ctl.agent_memory_events;
CREATE TRIGGER agent_memory_events_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.agent_memory_events FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();

ALTER TABLE dq_ctl.agent_blackboard ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bb_scope ON dq_ctl.agent_blackboard;
CREATE POLICY bb_scope ON dq_ctl.agent_blackboard FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS bb_sys ON dq_ctl.agent_blackboard;
CREATE POLICY bb_sys ON dq_ctl.agent_blackboard FOR SELECT TO dq_eval_svc, dq_seal_svc, dq_triage_svc, dq_publish_svc, dq_verify_svc, dq_monitor_svc, dq_auditor USING (true);
ALTER TABLE dq_ctl.agent_memory ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mem_scope ON dq_ctl.agent_memory;
CREATE POLICY mem_scope ON dq_ctl.agent_memory FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS mem_sys ON dq_ctl.agent_memory;
CREATE POLICY mem_sys ON dq_ctl.agent_memory FOR SELECT TO dq_eval_svc, dq_triage_svc, dq_publish_svc, dq_verify_svc, dq_monitor_svc, dq_auditor USING (true);

-- the triage and monitor seats read evidence (never write it)
DROP POLICY IF EXISTS units_triage ON dq_ctl.units;
CREATE POLICY units_triage ON dq_ctl.units FOR SELECT TO dq_triage_svc, dq_monitor_svc USING (true);
DROP POLICY IF EXISTS results_triage ON dq_ctl.results;
CREATE POLICY results_triage ON dq_ctl.results FOR SELECT TO dq_triage_svc, dq_monitor_svc USING (true);
DROP POLICY IF EXISTS abc_triage ON dq_ctl.abc;
CREATE POLICY abc_triage ON dq_ctl.abc FOR SELECT TO dq_triage_svc, dq_monitor_svc USING (true);
DROP POLICY IF EXISTS measure_triage ON dq_ctl.measure_results;
CREATE POLICY measure_triage ON dq_ctl.measure_results FOR SELECT TO dq_triage_svc, dq_monitor_svc USING (true);
DROP POLICY IF EXISTS decisions_triage ON dq_ctl.decisions;
CREATE POLICY decisions_triage ON dq_ctl.decisions FOR SELECT TO dq_triage_svc, dq_monitor_svc USING (true);
DROP POLICY IF EXISTS unit_events_triage ON dq_ctl.unit_events;
CREATE POLICY unit_events_triage ON dq_ctl.unit_events FOR SELECT TO dq_triage_svc, dq_monitor_svc USING (true);

REVOKE ALL ON dq_ctl.agent_blackboard, dq_ctl.agent_blackboard_head, dq_ctl.agent_memory, dq_ctl.agent_memory_events FROM PUBLIC;
GRANT SELECT ON dq_ctl.agent_blackboard, dq_ctl.agent_memory, dq_ctl.agent_memory_events, dq_ctl.agent_memory_current,
  dq_ctl.agent_state TO dq_api, dq_eval_svc, dq_triage_svc, dq_publish_svc, dq_verify_svc, dq_monitor_svc, dq_auditor;
GRANT SELECT ON dq_ctl.units, dq_ctl.results, dq_ctl.abc, dq_ctl.measure_results, dq_ctl.decisions, dq_ctl.unit_events,
  dq_ctl.batches TO dq_triage_svc, dq_monitor_svc;
GRANT USAGE ON SCHEMA dq_ctl TO dq_triage_svc, dq_monitor_svc;
REVOKE ALL ON FUNCTION dq_ctl._bb_append(text,text,text,text,text,text,text,text,text,jsonb,text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION dq_ctl.bb_post(text,text,text,text,text,text,jsonb,text[],text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.bb_post(text,text,text,text,text,text,jsonb,text[],text,text)
  TO dq_api, dq_eval_svc, dq_seal_svc, dq_triage_svc, dq_publish_svc, dq_verify_svc, dq_registry_svc, dq_monitor_svc;
REVOKE ALL ON FUNCTION dq_ctl.mem_pin_baseline(text,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.mem_pin_baseline(text,text,text,jsonb,text) TO dq_triage_svc, dq_publish_svc, dq_monitor_svc;
REVOKE ALL ON FUNCTION dq_ctl.mem_propose_known_issue(text,text,text,jsonb,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.mem_propose_known_issue(text,text,text,jsonb,text,timestamptz) TO dq_api;
REVOKE ALL ON FUNCTION dq_ctl.mem_decide_known_issue(bigint,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.mem_decide_known_issue(bigint,text,text,text) TO dq_api;
REVOKE ALL ON FUNCTION dq_ctl.bb_verify() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.bb_verify() TO dq_auditor, dq_verify_svc, dq_triage_svc, dq_monitor_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.bb_hash(text,bigint,text,text,text,text,text,text,text,text,text,jsonb,text[])
  TO dq_auditor, dq_verify_svc, dq_triage_svc, dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_monitor_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.in_scope(text), dq_ctl.agent_body_ok(jsonb), dq_ctl.refs_ok(text[]),
  dq_ctl.seat_agent(text), dq_ctl.seat_name(text)
  TO dq_triage_svc, dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_monitor_svc, dq_registry_svc, dq_auditor;
"""
