"""Two-person governance of configuration and rules (Part D, D1).

  dq_ctl.config_artifacts        append-only versions of each governed artifact of a state (OVERLAY, PROFILE, FEEDS,
                                 BINDINGS, POLICY, CONTRACTS, HARNESS, CATALOG digest). The EFFECTIVE configuration is
                                 the latest APPLIED version of each artifact; the runtime compiles only from it.
  dq_ctl.config_change_requests  a proposed set of artifact changes with the server-computed IMPACT (rule hashes and
                                 statuses before -> after), the base versions it was computed against, and whether it
                                 LOOSENS anything (then it needs two approvers).
  dq_ctl.config_cr_events        PROPOSED / APPROVED / REJECTED / STALE / APPLIED, each person-attributed; a step-up
                                 jti is single-use. Every event is chained into the CP audit log.

Write paths are SECURITY DEFINER functions only:
  gov_propose  (dq_api)            author = the verified session person
  gov_decide   (dq_api)            the database refuses the author and a repeat approver
  gov_apply    (dq_registry_svc)   needs the required number of distinct approvers; refuses if any base version moved
                                   (STALE); writes the artifact versions AND the rule approvals (registry_events) for
                                   the ruleset the approvers signed. No other role can write registry_events.
T1 caveat (red-team I5): the step-up token is verified by the api; the database enforces distinct person ids but
does not itself verify the token. Production verifies in the CP.
"""


def governance_ddl() -> str:
    return """
CREATE TABLE IF NOT EXISTS dq_ctl.config_artifacts(
  artifact_id text NOT NULL, version int NOT NULL, state text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('OVERLAY','PROFILE','FEEDS','BINDINGS','POLICY','CONTRACTS','HARNESS','CATALOG',
                                     'REVOCATION','AUTONOMY','AGENT','ENTITLEMENT','CDE','CLASSIFICATION','MONITOR',
                                     'PIPELINE','TASK_GATE','EVAL_CASE')),
  op text NOT NULL CHECK (op IN ('PUT','DELETE')), content jsonb, content_hash text NOT NULL, cr_id text NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY (artifact_id, version));
-- Part F P1-3 (K2a) / P2-1 (K11): re-render the kind CHECK so an already-applied schema accepts PIPELINE / TASK_GATE / EVAL_CASE
ALTER TABLE dq_ctl.config_artifacts DROP CONSTRAINT IF EXISTS config_artifacts_kind_check;
ALTER TABLE dq_ctl.config_artifacts ADD CONSTRAINT config_artifacts_kind_check CHECK (kind IN
  ('OVERLAY','PROFILE','FEEDS','BINDINGS','POLICY','CONTRACTS','HARNESS','CATALOG','REVOCATION','AUTONOMY','AGENT',
   'ENTITLEMENT','CDE','CLASSIFICATION','MONITOR','PIPELINE','TASK_GATE','EVAL_CASE'));
CREATE TABLE IF NOT EXISTS dq_ctl.config_change_requests(
  cr_id text PRIMARY KEY, state text NOT NULL, author text NOT NULL,
  origin text NOT NULL CHECK (origin IN ('HUMAN','PROPOSER','GENESIS','DEMOTION')), rationale text NOT NULL CHECK (length(rationale) >= 10),
  items jsonb NOT NULL, impact jsonb NOT NULL, base jsonb NOT NULL, loosening boolean NOT NULL,
  approvals_required int NOT NULL CHECK (approvals_required >= 1), signed_hash text NOT NULL,
  signed_rules jsonb NOT NULL, origin_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS dq_ctl.config_cr_events(
  id bigserial PRIMARY KEY, cr_id text NOT NULL REFERENCES dq_ctl.config_change_requests(cr_id),
  event text NOT NULL CHECK (event IN ('PROPOSED','APPROVED','REJECTED','STALE','APPLIED','WITHDRAWN','EXPIRED')),
  person_id text, jti text UNIQUE, detail jsonb, explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref)),
  at timestamptz NOT NULL DEFAULT clock_timestamp());
ALTER TABLE dq_ctl.config_cr_events ADD COLUMN IF NOT EXISTS explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref));

-- tenant settings (e.g. which organisation must approve configuration). Written at tenant onboarding.
CREATE TABLE IF NOT EXISTS dq_ctl.org_settings(key text PRIMARY KEY, value text NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp());

-- look-back: releases made under a rule version that was later REVOKED (flagged for human re-review)
CREATE TABLE IF NOT EXISTS dq_ctl.lookback_items(
  item_id bigserial PRIMARY KEY, cr_id text NOT NULL, unit_key text NOT NULL, batch_id text NOT NULL, node text NOT NULL,
  authority text NOT NULL, rule_artifact text NOT NULL, rule_hash text NOT NULL, released_at timestamptz,
  in_live_manifest boolean NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE (cr_id, unit_key, rule_artifact));
CREATE TABLE IF NOT EXISTS dq_ctl.lookback_events(
  id bigserial PRIMARY KEY, item_id bigint NOT NULL REFERENCES dq_ctl.lookback_items(item_id),
  event text NOT NULL CHECK (event IN ('OPEN','REASSESSED','ACCEPTED','WITHDRAWN')), person_id text, jti text UNIQUE,
  detail jsonb, explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref)),
  at timestamptz NOT NULL DEFAULT clock_timestamp());
ALTER TABLE dq_ctl.lookback_events ADD COLUMN IF NOT EXISTS explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref));
CREATE OR REPLACE VIEW dq_ctl.lookback_status WITH (security_invoker = true) AS
  SELECT i.*, (SELECT e.event FROM dq_ctl.lookback_events e WHERE e.item_id = i.item_id
               AND e.event IN ('ACCEPTED','WITHDRAWN') ORDER BY e.id DESC LIMIT 1) AS resolution,
         (SELECT e.detail FROM dq_ctl.lookback_events e WHERE e.item_id = i.item_id AND e.event = 'REASSESSED'
          ORDER BY e.id DESC LIMIT 1) AS reassessment
  FROM dq_ctl.lookback_items i;

DROP FUNCTION IF EXISTS dq_ctl.lookback_decide(bigint,text,text,text,text);
CREATE OR REPLACE FUNCTION dq_ctl.lookback_decide(p_item bigint, p_person text, p_jti text, p_verdict text, p_rationale text,
  p_explanation jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE i record; auth text;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  SELECT * INTO i FROM dq_ctl.lookback_items WHERE item_id = p_item;
  IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.unit_key = i.unit_key
                              AND dq_ctl.unit_in_scope(u.authorities)) THEN      -- EVERY authority of the unit
    RAISE EXCEPTION 'unknown look-back item' USING ERRCODE = 'no_data_found';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.org_settings WHERE key = 'approver_org') AND NOT EXISTS (
       SELECT 1 FROM dq_ctl.roster_current g WHERE g.person_id = p_person
         AND g.org = (SELECT value FROM dq_ctl.org_settings WHERE key = 'approver_org')) THEN
    RAISE EXCEPTION 'look-back items are resolved by the % organisation', (SELECT value FROM dq_ctl.org_settings
      WHERE key = 'approver_org') USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.lookback_events WHERE item_id = p_item AND event IN ('ACCEPTED','WITHDRAWN')) THEN
    RAISE EXCEPTION 'already resolved' USING ERRCODE = 'unique_violation';
  END IF;
  IF p_verdict NOT IN ('ACCEPTED','WITHDRAWN') OR length(coalesce(p_rationale,'')) < 20 OR p_jti IS NULL THEN
    RAISE EXCEPTION 'verdict ACCEPTED | WITHDRAWN with a rationale of at least 20 characters' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.lookback_events(item_id,event,person_id,jti,detail,explanation_ref)
    VALUES (p_item, p_verdict, p_person, p_jti, jsonb_build_object('rationale_sha256', encode(sha256(convert_to(p_rationale,'UTF8')),'hex')),
            p_explanation);
  IF p_verdict = 'WITHDRAWN' THEN      -- the existing DENY path removes it from the released views (breaker clause 6)
    INSERT INTO dq_ctl.deny(unit_key, reason, mechanism) VALUES (i.unit_key, 'look-back: released under revoked rule version', 'LOOKBACK');
    -- Part E §1.2: a withdrawn unit the agent released ON ITS OWN (L3 / L4: straight from EVALUATING, no person)
    -- drops that authority's RELEASE tier to L2. Lower-only; a tier already at or below L2 is left alone.
    IF EXISTS (SELECT 1 FROM dq_ctl.unit_events e WHERE e.unit_key = i.unit_key AND e.to_state = 'RELEASED'
               AND e.from_state = 'EVALUATING') THEN
      -- E8: only the state whose revocation raised the look-back (the revoking change request's state)
      FOR auth IN SELECT unnest(u.authorities) FROM dq_ctl.units u WHERE u.unit_key = i.unit_key LOOP
        PERFORM dq_ctl._autonomy_demote((SELECT r.state FROM dq_ctl.config_change_requests r WHERE r.cr_id = i.cr_id),
                                        dq_ctl.seat_agent('dq_publish_svc'), 'RELEASE', auth, 'L2',
                                        'LOOKBACK_WITHDRAWN', 'lookback:' || p_item, false);
      END LOOP;
    END IF;
  END IF;
  PERFORM dq_ctl.append_audit('LOOKBACK_'||p_verdict, encode(sha256(convert_to(p_item::text||'|'||p_person,'UTF8')),'hex'));
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.lookback_reassessed(p_item bigint, p_person text, p_detail jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NOT EXISTS (SELECT 1 FROM dq_ctl.lookback_items i JOIN dq_ctl.units u USING (unit_key)
                 WHERE i.item_id = p_item AND dq_ctl.unit_in_scope(u.authorities)) THEN
    RAISE EXCEPTION 'unknown look-back item' USING ERRCODE = 'no_data_found';
  END IF;
  INSERT INTO dq_ctl.lookback_events(item_id,event,person_id,detail) VALUES (p_item,'REASSESSED',p_person,p_detail);
END $$;

CREATE OR REPLACE VIEW dq_ctl.config_effective AS
  SELECT DISTINCT ON (artifact_id) * FROM dq_ctl.config_artifacts ORDER BY artifact_id, version DESC;
CREATE OR REPLACE VIEW dq_ctl.config_cr_status AS
  SELECT r.cr_id, r.state, r.author, r.origin, r.loosening, r.approvals_required, r.at,
         (SELECT e.event FROM dq_ctl.config_cr_events e WHERE e.cr_id = r.cr_id
           AND e.event IN ('REJECTED','STALE','APPLIED','WITHDRAWN','EXPIRED') ORDER BY e.id DESC LIMIT 1) AS terminal,
         (SELECT count(DISTINCT e.person_id) FROM dq_ctl.config_cr_events e WHERE e.cr_id = r.cr_id
           AND e.event = 'APPROVED') AS approvals
  FROM dq_ctl.config_change_requests r;

CREATE OR REPLACE FUNCTION dq_ctl.gov_propose(p_cr text, p_state text, p_author text, p_origin text, p_rationale text,
  p_items jsonb, p_impact jsonb, p_base jsonb, p_loosening boolean, p_signed text, p_rules jsonb, p_detail jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE lower_only boolean;
BEGIN
  IF session_user NOT IN ('dq_api') OR p_author IS NULL THEN
    RAISE EXCEPTION 'change requests are proposed by a person through the api' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_origin NOT IN ('HUMAN','PROPOSER','GENESIS') THEN
    RAISE EXCEPTION 'origin % is not proposed by a person', p_origin USING ERRCODE = 'check_violation';
  END IF;
  -- Part E (C4): for AUTONOMY / ENTITLEMENT / AGENT / CLASSIFICATION the DATABASE decides whether one approver is
  -- enough. Only a change SQL proves lower-only gets one; the caller's flag can make it stricter, never looser.
  -- Part F P1-3 (K2a): p_origin is threaded through so gov_item_valid can refuse a TASK_GATE release_ceiling of
  -- INHERIT unless this change request's own origin is GENESIS (resolved K2a-1).
  PERFORM dq_ctl.gov_item_valid(p_state, x, p_origin) FROM jsonb_array_elements(p_items) x;
  lower_only := dq_ctl.gov_items_lower_only(p_state, p_items);
  INSERT INTO dq_ctl.config_change_requests(cr_id,state,author,origin,rationale,items,impact,base,loosening,
      approvals_required,signed_hash,signed_rules,origin_detail)
    VALUES (p_cr,p_state,p_author,p_origin,p_rationale,p_items,p_impact,p_base,p_loosening OR NOT lower_only,
            CASE WHEN p_loosening OR NOT lower_only THEN 2 ELSE 1 END, p_signed, p_rules, coalesce(p_detail,'{}'::jsonb));
  INSERT INTO dq_ctl.config_cr_events(cr_id,event,person_id) VALUES (p_cr,'PROPOSED',p_author);
  PERFORM dq_ctl.append_audit('CONFIG_CR_PROPOSED', encode(sha256(convert_to(p_cr||'|'||p_signed,'UTF8')),'hex'));
END $$;

DROP FUNCTION IF EXISTS dq_ctl.gov_decide(text,text,text,text);
CREATE OR REPLACE FUNCTION dq_ctl.gov_decide(p_cr text, p_person text, p_jti text, p_verdict text,
  p_explanation jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE r record; st record; refusal text;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'decided through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  SELECT * INTO r FROM dq_ctl.config_change_requests WHERE cr_id = p_cr FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown change request' USING ERRCODE = 'no_data_found'; END IF;
  SELECT * INTO st FROM dq_ctl.config_cr_status WHERE cr_id = p_cr;
  IF st.terminal IS NOT NULL THEN RAISE EXCEPTION 'change request is %', st.terminal USING ERRCODE = 'check_violation'; END IF;
  IF r.author = p_person THEN
    RAISE EXCEPTION 'two-person rule: the author cannot approve or reject their own change' USING ERRCODE = 'check_violation';
  END IF;
  IF r.origin = 'DEMOTION' THEN
    RAISE EXCEPTION 'an automatic demotion is applied, not decided' USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.org_settings WHERE key = 'approver_org') AND NOT EXISTS (
       SELECT 1 FROM dq_ctl.roster_current g WHERE g.person_id = p_person AND g.duty = 'governance'
         AND g.org = (SELECT value FROM dq_ctl.org_settings WHERE key = 'approver_org')) THEN
    RAISE EXCEPTION 'configuration is approved by the % organisation; this person is not in it',
      (SELECT value FROM dq_ctl.org_settings WHERE key = 'approver_org') USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.config_cr_events WHERE cr_id = p_cr AND person_id = p_person
             AND event IN ('APPROVED','REJECTED')) THEN
    RAISE EXCEPTION 'this person already decided this change request' USING ERRCODE = 'unique_violation';
  END IF;
  IF p_verdict NOT IN ('APPROVED','REJECTED') OR p_jti IS NULL THEN RAISE EXCEPTION 'bad verdict' USING ERRCODE = 'check_violation'; END IF;
  -- Part F R4-5: an APPROVAL of a promotion needs complete evidence and the minimum record (trace_schema). The
  -- GENESIS seed grant is exempted (it is ratified, not an evidence-backed operational promotion) -- the origin
  -- comes from the stored change request row, never from the caller.
  IF p_verdict = 'APPROVED' THEN
    refusal := dq_ctl.promotion_refusal(r.state, r.items, r.origin);
    IF refusal IS NOT NULL THEN RAISE EXCEPTION '%', refusal USING ERRCODE = 'check_violation'; END IF;
    -- Part F P1-4 (K3-3, RESOLVED): a TASK_GATE mode promotion ALSO needs a linked backtest report
    -- (dq_ctl.cr_evidence); a SEPARATE check from promotion_refusal above (K3_DESIGN.md §6 item 4).
    refusal := dq_ctl.mode_promotion_refusal(p_cr, r.items);
    IF refusal IS NOT NULL THEN RAISE EXCEPTION '%', refusal USING ERRCODE = 'check_violation'; END IF;
    -- K9 (ORCH-1 step b): a SEPARATE check, sibling to mode_promotion_refusal above, for a TASK_GATE
    -- release_ceiling RISE (K7_K8_K9_DESIGN.md §3.3 point 2).
    refusal := dq_ctl.ceiling_promotion_refusal(p_cr, r.items);
    IF refusal IS NOT NULL THEN RAISE EXCEPTION '%', refusal USING ERRCODE = 'check_violation'; END IF;
  END IF;
  INSERT INTO dq_ctl.config_cr_events(cr_id,event,person_id,jti,explanation_ref) VALUES (p_cr,p_verdict,p_person,p_jti,p_explanation);
  PERFORM dq_ctl.append_audit('CONFIG_CR_'||p_verdict, encode(sha256(convert_to(p_cr||'|'||p_person||'|'||r.signed_hash,'UTF8')),'hex'));
END $$;

-- Applies the SIGNED request: artifacts from r.items, rule approvals from r.signed_rules (post-build F5). The caller
-- supplies only the base it recomputed NOW; any difference from the signed base means STALE (I1). One apply per
-- state at a time (advisory lock, F6). Approvers must still hold the governance grant at apply time.
-- Part F R4-2: applying a GENESIS request binds the control plane's deployment (dq_ctl.bind_deployment derives the
-- genesis kind itself) in the SAME transaction; p_deployment is the deployment the applying runtime states.
DROP FUNCTION IF EXISTS dq_ctl.gov_apply(text,jsonb);
DROP FUNCTION IF EXISTS dq_ctl.gov_apply(text,jsonb,text);
CREATE OR REPLACE FUNCTION dq_ctl.gov_apply(p_cr text, p_base jsonb, p_deployment text DEFAULT NULL,
  p_idp_kind text DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE r record; st record; a jsonb; cur int; k text; v jsonb; n int; refusal text;
BEGIN
  IF session_user <> 'dq_registry_svc' THEN RAISE EXCEPTION 'only the registry service applies' USING ERRCODE = 'insufficient_privilege'; END IF;
  SELECT * INTO r FROM dq_ctl.config_change_requests WHERE cr_id = p_cr FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown change request' USING ERRCODE = 'no_data_found'; END IF;
  PERFORM pg_advisory_xact_lock(hashtext('dq-config:' || r.state));
  SELECT * INTO st FROM dq_ctl.config_cr_status WHERE cr_id = p_cr;
  IF st.terminal IS NOT NULL THEN RETURN st.terminal; END IF;
  IF r.at < clock_timestamp() - interval '14 days' THEN
    INSERT INTO dq_ctl.config_cr_events(cr_id,event) VALUES (p_cr,'EXPIRED'); RETURN 'EXPIRED';
  END IF;
  SELECT count(DISTINCT e.person_id) INTO n FROM dq_ctl.config_cr_events e
   WHERE e.cr_id = p_cr AND e.event = 'APPROVED' AND e.person_id <> r.author
     AND EXISTS (SELECT 1 FROM dq_ctl.roster_current g WHERE g.person_id = e.person_id AND g.duty = 'governance'
                 AND (NOT EXISTS (SELECT 1 FROM dq_ctl.org_settings WHERE key = 'approver_org')
                      OR g.org = (SELECT value FROM dq_ctl.org_settings WHERE key = 'approver_org')));
  -- Part E (C4): re-derived at apply; the stored requirement can only be raised, never lowered
  IF n < greatest(r.approvals_required, CASE WHEN dq_ctl.gov_items_lower_only(r.state, r.items) THEN 1 ELSE 2 END) THEN
    RETURN 'AWAITING_APPROVAL';
  END IF;
  FOR k, v IN SELECT * FROM jsonb_each(r.base->'versions') LOOP
    SELECT max(version) INTO cur FROM dq_ctl.config_artifacts WHERE artifact_id = k;
    IF coalesce(cur, 0) <> (v #>> '{}')::int THEN
      INSERT INTO dq_ctl.config_cr_events(cr_id,event,detail) VALUES (p_cr,'STALE',jsonb_build_object('artifact',k));
      RETURN 'STALE';
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM dq_ctl.config_effective WHERE state = r.state) <> (SELECT count(*) FROM jsonb_object_keys(r.base->'versions')) THEN
    INSERT INTO dq_ctl.config_cr_events(cr_id,event,detail) VALUES (p_cr,'STALE',jsonb_build_object('reason','artifact set moved'));
    RETURN 'STALE';
  END IF;
  IF p_base IS DISTINCT FROM r.base THEN
    INSERT INTO dq_ctl.config_cr_events(cr_id,event,detail) VALUES (p_cr,'STALE',jsonb_build_object('reason','recompiled base differs'));
    RETURN 'STALE';
  END IF;
  -- Part F R4-5: re-checked at APPLY (the evidence or the record may have changed since the approvals); GENESIS
  -- stays exempted here too, for the same reason as gov_decide above.
  refusal := dq_ctl.promotion_refusal(r.state, r.items, r.origin);
  IF refusal IS NOT NULL THEN RAISE EXCEPTION '%', refusal USING ERRCODE = 'check_violation'; END IF;
  -- Part F P1-4 (K3-3, RESOLVED): re-checked at APPLY too, for the same reason as promotion_refusal above --
  -- the evidence link may have changed (or never existed) since the approvals.
  refusal := dq_ctl.mode_promotion_refusal(p_cr, r.items);
  IF refusal IS NOT NULL THEN RAISE EXCEPTION '%', refusal USING ERRCODE = 'check_violation'; END IF;
  -- K9: re-checked at APPLY too, for the same reason as mode_promotion_refusal above -- the evidence link may
  -- have changed (or never existed) since the approvals.
  refusal := dq_ctl.ceiling_promotion_refusal(p_cr, r.items);
  IF refusal IS NOT NULL THEN RAISE EXCEPTION '%', refusal USING ERRCODE = 'check_violation'; END IF;
  FOR a IN SELECT * FROM jsonb_array_elements(r.items) LOOP
    SELECT coalesce(max(version), 0) INTO cur FROM dq_ctl.config_artifacts WHERE artifact_id = a->>'artifact_id';
    INSERT INTO dq_ctl.config_artifacts(artifact_id,version,state,kind,op,content,content_hash,cr_id)
      VALUES (a->>'artifact_id', cur + 1, r.state, a->>'kind', a->>'op', a->'content', a->>'content_hash', p_cr);
  END LOOP;
  -- Part F P1-3 (K2a), ORCH-1 (C1): the SAME helper the TEST genesis path calls, with the SAME items, now that
  -- config_effective reflects this CR's own artifact versions (so a deleted introducer in THIS batch already
  -- reads as not-effective below)
  PERFORM dq_ctl._introduce_datasets(r.state, r.items, p_cr);
  -- approve only the versions that CHANGE: an untouched rule keeps the request that approved its version
  FOR k, v IN SELECT * FROM jsonb_each(r.signed_rules) LOOP
    -- a REVOKED version is never approved again (red-team C1): a fix must be a NEW version (new hash)
    IF (SELECT e.semantic_hash || '|' || e.state FROM dq_ctl.registry_events e WHERE e.artifact_id = k
        ORDER BY e.id DESC LIMIT 1) IS DISTINCT FROM ((v->>'hash') || '|APPROVED')
       AND NOT EXISTS (SELECT 1 FROM dq_ctl.registry_events e WHERE e.artifact_id = k AND e.semantic_hash = v->>'hash'
                       AND e.state = 'REVOKED')
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.items) x WHERE x->>'kind' = 'REVOCATION'
                       AND x->'content'->>'rule' = k AND x->'content'->>'hash' = v->>'hash') THEN
      INSERT INTO dq_ctl.registry_events(artifact_kind,artifact_id,semantic_hash,state,decision_ref,rule_meta)
        VALUES ('rule', k, v->>'hash', 'APPROVED', p_cr, v - 'hash');
    END IF;
  END LOOP;
  -- governed revocations: the version stops being approved (clause 2 then blocks new releases); the look-back
  -- over EARLIER releases under it is opened by the registry service after this apply
  FOR a IN SELECT * FROM jsonb_array_elements(r.items) LOOP
    IF a->>'kind' = 'REVOCATION' THEN
      INSERT INTO dq_ctl.registry_events(artifact_kind,artifact_id,semantic_hash,state,decision_ref)
        VALUES ('rule', a->'content'->>'rule', a->'content'->>'hash', 'REVOKED', p_cr);
    END IF;
  END LOOP;
  INSERT INTO dq_ctl.config_cr_events(cr_id,event) VALUES (p_cr,'APPLIED');
  IF r.origin = 'GENESIS' THEN                  -- R4-2: refuses (and rolls the apply back) on a disallowed deployment
    PERFORM dq_ctl.bind_deployment(p_deployment, p_idp_kind);
  END IF;
  PERFORM dq_ctl.append_audit('CONFIG_CR_APPLIED', encode(sha256(convert_to(p_cr||'|'||r.signed_hash,'UTF8')),'hex'));
  RETURN 'APPLIED';
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.gov_withdraw(p_cr text, p_person text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE r record;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  SELECT * INTO r FROM dq_ctl.config_change_requests WHERE cr_id = p_cr;
  IF r.author <> p_person THEN RAISE EXCEPTION 'only the author withdraws' USING ERRCODE = 'check_violation'; END IF;
  IF (SELECT terminal FROM dq_ctl.config_cr_status WHERE cr_id = p_cr) IS NOT NULL THEN
    RAISE EXCEPTION 'already terminal' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.config_cr_events(cr_id,event,person_id) VALUES (p_cr,'WITHDRAWN',p_person);
END $$;

DROP TRIGGER IF EXISTS config_artifacts_no_mut ON dq_ctl.config_artifacts;
CREATE TRIGGER config_artifacts_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.config_artifacts FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS config_crs_no_mut ON dq_ctl.config_change_requests;
CREATE TRIGGER config_crs_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.config_change_requests FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS config_cr_events_no_mut ON dq_ctl.config_cr_events;
CREATE TRIGGER config_cr_events_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.config_cr_events FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();

-- ===== Part F P1-3 (K2a), ORCH-1: ceilings apply only to datasets a gate introduces (C1, I3) ====================
-- Append-only; written ONLY by dq_ctl._introduce_datasets (below), which is owner-only (no login role holds
-- EXECUTE on it). One row per (state, dataset) version: the gate that currently governs that dataset's ceiling.
CREATE TABLE IF NOT EXISTS dq_ctl.gated_dataset_introductions(state text NOT NULL, dataset text NOT NULL, version int NOT NULL,
  introduced_by text NOT NULL, gate_version int NOT NULL, cr_id text NOT NULL,
  reason text NOT NULL CHECK (reason IN ('FIRST','INTRODUCER_REMOVED','REINTRODUCED')),
  at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY (state, dataset, version));
DROP TRIGGER IF EXISTS gated_dataset_introductions_no_mut ON dq_ctl.gated_dataset_introductions;
CREATE TRIGGER gated_dataset_introductions_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.gated_dataset_introductions
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS gated_dataset_introductions_no_trunc ON dq_ctl.gated_dataset_introductions;
CREATE TRIGGER gated_dataset_introductions_no_trunc BEFORE TRUNCATE ON dq_ctl.gated_dataset_introductions
  FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation();
REVOKE ALL ON dq_ctl.gated_dataset_introductions FROM PUBLIC;
ALTER TABLE dq_ctl.gated_dataset_introductions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.gated_dataset_introductions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gated_dataset_introductions_read ON dq_ctl.gated_dataset_introductions;
CREATE POLICY gated_dataset_introductions_read ON dq_ctl.gated_dataset_introductions FOR SELECT
  TO dq_api, dq_registry_svc, dq_eval_svc, dq_publish_svc, dq_auditor, dq_owner USING (true);
DROP POLICY IF EXISTS gated_dataset_introductions_owner_ins ON dq_ctl.gated_dataset_introductions;
CREATE POLICY gated_dataset_introductions_owner_ins ON dq_ctl.gated_dataset_introductions FOR INSERT TO dq_owner WITH CHECK (true);
GRANT SELECT ON dq_ctl.gated_dataset_introductions TO dq_api, dq_registry_svc, dq_eval_svc, dq_publish_svc, dq_auditor;

-- Owner-only (no GRANT below, to any role): the one helper BOTH genesis paths call with the SAME items, in the
-- SAME order (C1). Runs over TASK_GATE items, DELETEs before PUTs (both in item order); for each dataset of a PUT:
-- no row yet -> FIRST; the latest row's introducing gate is no longer effective (deleted) -> INTRODUCER_REMOVED;
-- explicitly listed in this item's content.reintroduce -> REINTRODUCED (gov_item_lower_only already refuses to
-- call this lower-only, so a REINTRODUCED item always needed two approvers before apply reached here).
CREATE OR REPLACE FUNCTION dq_ctl._introduce_datasets(p_state text, p_items jsonb, p_cr text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE it jsonb; ds text; latest record; gv int;
BEGIN
  FOR it IN SELECT * FROM jsonb_array_elements(p_items) WHERE value->>'kind' = 'TASK_GATE' AND value->>'op' = 'PUT' LOOP
    gv := (SELECT coalesce(max(version), 0) FROM dq_ctl.config_artifacts WHERE artifact_id = it->>'artifact_id');
    FOR ds IN SELECT jsonb_array_elements_text(it->'content'->'datasets') LOOP
      SELECT * INTO latest FROM dq_ctl.gated_dataset_introductions
       WHERE state = p_state AND dataset = ds ORDER BY version DESC LIMIT 1;
      IF NOT FOUND THEN
        INSERT INTO dq_ctl.gated_dataset_introductions(state,dataset,version,introduced_by,gate_version,cr_id,reason)
          VALUES (p_state, ds, 1, it->>'artifact_id', gv, p_cr, 'FIRST');
      ELSIF NOT EXISTS (SELECT 1 FROM dq_ctl.config_effective c WHERE c.artifact_id = latest.introduced_by AND c.op = 'PUT') THEN
        INSERT INTO dq_ctl.gated_dataset_introductions(state,dataset,version,introduced_by,gate_version,cr_id,reason)
          VALUES (p_state, ds, latest.version + 1, it->>'artifact_id', gv, p_cr, 'INTRODUCER_REMOVED');
      ELSIF coalesce(it->'content'->'reintroduce', '[]'::jsonb) ? ds THEN
        INSERT INTO dq_ctl.gated_dataset_introductions(state,dataset,version,introduced_by,gate_version,cr_id,reason)
          VALUES (p_state, ds, latest.version + 1, it->>'artifact_id', gv, p_cr, 'REINTRODUCED');
      END IF;
    END LOOP;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION dq_ctl._introduce_datasets(text,jsonb,text) FROM PUBLIC;

-- ===== Part F P1-4 (K3): backtest reports and CR evidence (PFP :903-906) ========================================
-- Append-only. The ONLY write path from the backtest process into the live control plane is dq_ctl.backtest_put
-- (below); it writes both tables in the same transaction, SYNTHESIZED-labelled, linking the report to the CR it
-- backtested so gov_decide / gov_apply can require it before a TASK_GATE mode promotion (K3_DESIGN.md §6 item 4).
CREATE TABLE IF NOT EXISTS dq_ctl.backtest_reports(
  report_id text PRIMARY KEY CHECK (report_id ~ '^BTR-[0-9a-f]{12}$'),
  cr_id text NOT NULL REFERENCES dq_ctl.config_change_requests(cr_id),
  report jsonb NOT NULL, label text NOT NULL CHECK (label = 'SYNTHESIZED'),
  digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
  at timestamptz NOT NULL DEFAULT clock_timestamp());
DROP TRIGGER IF EXISTS backtest_reports_no_mut ON dq_ctl.backtest_reports;
CREATE TRIGGER backtest_reports_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.backtest_reports
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS backtest_reports_no_trunc ON dq_ctl.backtest_reports;
CREATE TRIGGER backtest_reports_no_trunc BEFORE TRUNCATE ON dq_ctl.backtest_reports
  FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation();
REVOKE ALL ON dq_ctl.backtest_reports FROM PUBLIC;

-- cr_evidence (PFP :905, exact columns): one row per linked backtest report. items_digest is computed by the
-- DATABASE (dq_ctl._items_digest below), over the CR's own stored items -- never caller-asserted, so gov_decide /
-- gov_apply can compare it against the SAME items column at refusal-check time and trust the match.
CREATE TABLE IF NOT EXISTS dq_ctl.cr_evidence(
  id bigserial PRIMARY KEY, cr_id text NOT NULL REFERENCES dq_ctl.config_change_requests(cr_id),
  report_id text NOT NULL REFERENCES dq_ctl.backtest_reports(report_id),
  items_digest text NOT NULL CHECK (items_digest ~ '^[0-9a-f]{64}$'),
  linked_by text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());
DROP TRIGGER IF EXISTS cr_evidence_no_mut ON dq_ctl.cr_evidence;
CREATE TRIGGER cr_evidence_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.cr_evidence
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS cr_evidence_no_trunc ON dq_ctl.cr_evidence;
CREATE TRIGGER cr_evidence_no_trunc BEFORE TRUNCATE ON dq_ctl.cr_evidence
  FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation();
REVOKE ALL ON dq_ctl.cr_evidence FROM PUBLIC;
GRANT SELECT ON dq_ctl.backtest_reports, dq_ctl.cr_evidence TO dq_api, dq_registry_svc, dq_auditor;

-- sha256(items) (PFP :906), the one definition both backtest_put (writer) and mode_promotion_refusal (reader)
-- use, so a row's items_digest is always comparable against the SAME CR's current items column.
CREATE OR REPLACE FUNCTION dq_ctl._items_digest(p_items jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT encode(sha256(convert_to(p_items::text, 'UTF8')), 'hex') $$;

-- the ONLY write path from the backtest process into the live control plane (PFP :904). dq_api, SECURITY DEFINER:
-- writes the report AND links it as evidence to its own cr_id in the SAME transaction (linked_by: this is a
-- system-computed link, no person signs it -- the same SYSTEM: convention _autonomy_demote already uses).
CREATE OR REPLACE FUNCTION dq_ctl.backtest_put(p_report_id text, p_cr_id text, p_report jsonb, p_label text,
  p_digest text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE items jsonb;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'backtest reports are written through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF p_label <> 'SYNTHESIZED' THEN RAISE EXCEPTION 'a backtest report is labelled SYNTHESIZED' USING ERRCODE = 'check_violation'; END IF;
  SELECT r.items INTO items FROM dq_ctl.config_change_requests r WHERE r.cr_id = p_cr_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown change request %', p_cr_id USING ERRCODE = 'no_data_found'; END IF;
  INSERT INTO dq_ctl.backtest_reports(report_id, cr_id, report, label, digest)
    VALUES (p_report_id, p_cr_id, p_report, p_label, p_digest);
  INSERT INTO dq_ctl.cr_evidence(cr_id, report_id, items_digest, linked_by)
    VALUES (p_cr_id, p_report_id, dq_ctl._items_digest(items), 'SYSTEM:BACKTEST');
  PERFORM dq_ctl.append_audit('BACKTEST_REPORT_STORED',
    encode(sha256(convert_to(p_report_id || '|' || p_cr_id || '|' || p_digest, 'UTF8')), 'hex'));
  RETURN p_report_id;
END $$;
REVOKE ALL ON FUNCTION dq_ctl.backtest_put(text,text,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.backtest_put(text,text,jsonb,text,text) TO dq_api;

-- K3-3 (RESOLVED, K3_DESIGN.md §6 item 4): a SEPARATE function, parallel to promotion_refusal, not a widening of
-- it -- the predicate (a TASK_GATE mode change) is unrelated to AUTONOMY rank-rises. NULL when every TASK_GATE PUT
-- item in p_items that changes an EXISTING gate's `mode` has a dq_ctl.cr_evidence row linking p_cr with an
-- items_digest matching THESE SAME items; else the first refusal. A brand-new gate (old IS NULL, e.g. genesis or
-- the first wiring of a gate) is not a MODE CHANGE and needs no evidence. The message starts with "promotion
-- requires" ON PURPOSE: registry.apply()'s own exception-translation (registry.py:737) already discriminates an
-- R4-5-style promotion refusal (-> Denied) from an R4-2 deployment-binding refusal (-> re-raised) by that exact
-- prefix; reusing it means this seat touches governance_schema.py only, not registry.py.
DROP FUNCTION IF EXISTS dq_ctl.mode_promotion_refusal(text,jsonb);
CREATE OR REPLACE FUNCTION dq_ctl.mode_promotion_refusal(p_cr text, p_items jsonb) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE it jsonb; old jsonb;
BEGIN
  FOR it IN SELECT * FROM jsonb_array_elements(p_items) LOOP
    CONTINUE WHEN it->>'kind' <> 'TASK_GATE' OR it->>'op' <> 'PUT';
    SELECT c.content INTO old FROM dq_ctl.config_effective c WHERE c.artifact_id = it->>'artifact_id' AND c.op = 'PUT';
    CONTINUE WHEN old IS NULL;                                        -- a brand-new gate: nothing to promote FROM
    CONTINUE WHEN old->>'mode' IS NOT DISTINCT FROM it->'content'->>'mode';    -- not a mode change
    IF NOT EXISTS (SELECT 1 FROM dq_ctl.cr_evidence e WHERE e.cr_id = p_cr
                   AND e.items_digest = dq_ctl._items_digest(p_items)) THEN
      RETURN format('promotion requires a linked backtest report (dq_ctl.cr_evidence) for the TASK_GATE mode '
                    'change on %s', it->>'artifact_id');
    END IF;
  END LOOP;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION dq_ctl.mode_promotion_refusal(text,jsonb) FROM PUBLIC;

-- K9 (ORCH-1 step b, K7_K8_K9_DESIGN.md §3.3 point 2): a SIBLING to mode_promotion_refusal above, same
-- cr_evidence/_items_digest mechanism, but the predicate is a release_ceiling RISE (not a mode change). Lowering a
-- ceiling needs no evidence (demotion-is-automatic's own standing rule: tightening never needs proof). NULL when
-- every TASK_GATE PUT item in p_items whose release_ceiling RISES (strictly, by tier_rank) has a cr_evidence row
-- linking p_cr with an items_digest matching THESE SAME items; else the first refusal. A brand-new gate (old IS
-- NULL) or an INHERIT ceiling on either side (tier_rank NULL) is not a comparable rise and is skipped, same
-- NULL-safe stance as gov_item_valid's own ceiling-inert check above.
DROP FUNCTION IF EXISTS dq_ctl.ceiling_promotion_refusal(text,jsonb);
CREATE OR REPLACE FUNCTION dq_ctl.ceiling_promotion_refusal(p_cr text, p_items jsonb) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE it jsonb; old jsonb;
BEGIN
  FOR it IN SELECT * FROM jsonb_array_elements(p_items) LOOP
    CONTINUE WHEN it->>'kind' <> 'TASK_GATE' OR it->>'op' <> 'PUT';
    SELECT c.content INTO old FROM dq_ctl.config_effective c WHERE c.artifact_id = it->>'artifact_id' AND c.op = 'PUT';
    CONTINUE WHEN old IS NULL;                                        -- a brand-new gate: nothing to promote FROM
    CONTINUE WHEN dq_ctl.tier_rank(old->>'release_ceiling') IS NULL
                  OR dq_ctl.tier_rank(it->'content'->>'release_ceiling') IS NULL;   -- INHERIT either side: not comparable
    CONTINUE WHEN dq_ctl.tier_rank(it->'content'->>'release_ceiling') <= dq_ctl.tier_rank(old->>'release_ceiling');
    IF NOT EXISTS (SELECT 1 FROM dq_ctl.cr_evidence e WHERE e.cr_id = p_cr
                   AND e.items_digest = dq_ctl._items_digest(p_items)) THEN
      RETURN format('promotion requires a linked backtest report (dq_ctl.cr_evidence) for the TASK_GATE '
                    'release_ceiling rise on %s', it->>'artifact_id');
    END IF;
  END LOOP;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION dq_ctl.ceiling_promotion_refusal(text,jsonb) FROM PUBLIC;

-- ===== Part F P1-3 (K2b): dbt gate verdicts, discovery snapshots (PFP :816-824) ==================================
-- gate_checks: one row per (pipeline_id, task_id, run_key, gate_version), UNIQUE on those four columns (PFP :819).
-- Not append-only in the strict sense: a repeat poll of the SAME run (the verdict unchanged) updates `polls`/
-- `last_at` in place (WIRING_ARCHITECTURE.md §2, "the table grows with runs, not with polls"); a verdict CHANGE is
-- also recorded, immutably, as a new row in gate_check_transitions below. Writer: dq_discovery_svc only (PFP :819).
-- K2 is advisory-only (block-mode promotion is K3's scope): the verdict vocabulary is PASS / WOULD_HOLD, matching
-- trace_schema.ATTR_VOCAB's own K2-scoped `gate_verdict` reservation -- enforced here too (DB, not only Python).
CREATE TABLE IF NOT EXISTS dq_ctl.gate_checks(
  pipeline_id text NOT NULL, task_id text NOT NULL, run_key text NOT NULL, gate_version int NOT NULL,
  verdict text NOT NULL CHECK (verdict IN ('PASS','WOULD_HOLD')),
  first_at timestamptz NOT NULL DEFAULT clock_timestamp(), last_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  polls int NOT NULL DEFAULT 1 CHECK (polls >= 1),
  UNIQUE (pipeline_id, task_id, run_key, gate_version));
-- gate_check_transitions: append-only event log of a VERDICT CHANGE for one gate_checks row (K2b-4, resolved:
-- mirrors config_cr_events's append-only shape, minus person_id/jti since gate checks are system-written, not
-- person-decided). The FK to the parent is the SAME natural key gate_checks itself is unique on (there is no
-- single-column id on gate_checks to reference); "unit_key or batch_id" from the brief's literal text does not fit
-- this domain (a gate check is per pipeline/task/run, never per unit) and is not used here -- reported, not
-- silently substituted.
CREATE TABLE IF NOT EXISTS dq_ctl.gate_check_transitions(
  id bigserial PRIMARY KEY, pipeline_id text NOT NULL, task_id text NOT NULL, run_key text NOT NULL,
  gate_version int NOT NULL, verdict text NOT NULL CHECK (verdict IN ('PASS','WOULD_HOLD')),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb, at timestamptz NOT NULL DEFAULT clock_timestamp());
DROP TRIGGER IF EXISTS gate_check_transitions_no_mut ON dq_ctl.gate_check_transitions;
CREATE TRIGGER gate_check_transitions_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.gate_check_transitions
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS gate_check_transitions_no_trunc ON dq_ctl.gate_check_transitions;
CREATE TRIGGER gate_check_transitions_no_trunc BEFORE TRUNCATE ON dq_ctl.gate_check_transitions
  FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation();
REVOKE ALL ON dq_ctl.gate_checks, dq_ctl.gate_check_transitions FROM PUBLIC;
ALTER TABLE dq_ctl.gate_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.gate_checks FORCE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.gate_check_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.gate_check_transitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gate_checks_read ON dq_ctl.gate_checks;
CREATE POLICY gate_checks_read ON dq_ctl.gate_checks FOR SELECT
  TO dq_api, dq_registry_svc, dq_auditor, dq_discovery_svc, dq_owner USING (true);
DROP POLICY IF EXISTS gate_checks_svc_write ON dq_ctl.gate_checks;
CREATE POLICY gate_checks_svc_write ON dq_ctl.gate_checks FOR ALL TO dq_discovery_svc, dq_owner
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS gate_check_transitions_read ON dq_ctl.gate_check_transitions;
CREATE POLICY gate_check_transitions_read ON dq_ctl.gate_check_transitions FOR SELECT
  TO dq_api, dq_registry_svc, dq_auditor, dq_discovery_svc, dq_owner USING (true);
DROP POLICY IF EXISTS gate_check_transitions_svc_ins ON dq_ctl.gate_check_transitions;
CREATE POLICY gate_check_transitions_svc_ins ON dq_ctl.gate_check_transitions FOR INSERT
  TO dq_discovery_svc, dq_owner WITH CHECK (true);
GRANT SELECT ON dq_ctl.gate_checks, dq_ctl.gate_check_transitions TO dq_api, dq_registry_svc, dq_auditor;
GRANT SELECT, INSERT, UPDATE ON dq_ctl.gate_checks TO dq_discovery_svc;
GRANT SELECT, INSERT ON dq_ctl.gate_check_transitions TO dq_discovery_svc;
GRANT USAGE ON SEQUENCE dq_ctl.gate_check_transitions_id_seq TO dq_discovery_svc;
GRANT USAGE ON SCHEMA dq_ctl TO dq_discovery_svc;

-- dq_ctl.gate_check_record: the ONE writer of both tables (dq_discovery_svc only). Upsert on the natural key: a
-- repeat poll with the SAME verdict increments `polls`/bumps `last_at` in place; a verdict CHANGE updates the row
-- AND appends an immutable transition (WIRING_ARCHITECTURE.md §2).
CREATE OR REPLACE FUNCTION dq_ctl.gate_check_record(p_pipeline_id text, p_task_id text, p_run_key text,
  p_gate_version int, p_verdict text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE prior text;
BEGIN
  IF session_user <> 'dq_discovery_svc' THEN
    RAISE EXCEPTION 'gate checks are recorded by the discovery service' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT verdict INTO prior FROM dq_ctl.gate_checks
   WHERE pipeline_id = p_pipeline_id AND task_id = p_task_id AND run_key = p_run_key AND gate_version = p_gate_version;
  IF NOT FOUND THEN
    INSERT INTO dq_ctl.gate_checks(pipeline_id, task_id, run_key, gate_version, verdict)
      VALUES (p_pipeline_id, p_task_id, p_run_key, p_gate_version, p_verdict);
    RETURN;
  END IF;
  UPDATE dq_ctl.gate_checks SET verdict = p_verdict, last_at = clock_timestamp(),
    polls = polls + 1
   WHERE pipeline_id = p_pipeline_id AND task_id = p_task_id AND run_key = p_run_key AND gate_version = p_gate_version;
  IF prior IS DISTINCT FROM p_verdict THEN
    INSERT INTO dq_ctl.gate_check_transitions(pipeline_id, task_id, run_key, gate_version, verdict, detail)
      VALUES (p_pipeline_id, p_task_id, p_run_key, p_gate_version, p_verdict,
              jsonb_build_object('from', prior, 'to', p_verdict));
  END IF;
END $$;
REVOKE ALL ON FUNCTION dq_ctl.gate_check_record(text,text,text,int,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.gate_check_record(text,text,text,int,text) TO dq_discovery_svc;

-- dag_snapshots (K2b-5, REVERSED/RESOLVED: digest only, no persisted node list -- the Tower derives the live node
-- list from adapters.dag.dbt_files.discover() each time, never from history here). A row records only that a
-- manifest at this digest was discovered for this pipeline, for future drift detection (not built in K2).
CREATE TABLE IF NOT EXISTS dq_ctl.dag_snapshots(
  pipeline_id text NOT NULL, snapshot_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  manifest_digest text NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'), discovered_by text NOT NULL,
  PRIMARY KEY (pipeline_id, snapshot_at));
DROP TRIGGER IF EXISTS dag_snapshots_no_mut ON dq_ctl.dag_snapshots;
CREATE TRIGGER dag_snapshots_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.dag_snapshots
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS dag_snapshots_no_trunc ON dq_ctl.dag_snapshots;
CREATE TRIGGER dag_snapshots_no_trunc BEFORE TRUNCATE ON dq_ctl.dag_snapshots
  FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation();
REVOKE ALL ON dq_ctl.dag_snapshots FROM PUBLIC;
ALTER TABLE dq_ctl.dag_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE dq_ctl.dag_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dag_snapshots_read ON dq_ctl.dag_snapshots;
CREATE POLICY dag_snapshots_read ON dq_ctl.dag_snapshots FOR SELECT
  TO dq_api, dq_registry_svc, dq_auditor, dq_discovery_svc, dq_owner USING (true);
DROP POLICY IF EXISTS dag_snapshots_svc_ins ON dq_ctl.dag_snapshots;
CREATE POLICY dag_snapshots_svc_ins ON dq_ctl.dag_snapshots FOR INSERT TO dq_discovery_svc, dq_owner WITH CHECK (true);
GRANT SELECT ON dq_ctl.dag_snapshots TO dq_api, dq_registry_svc, dq_auditor;
GRANT SELECT, INSERT ON dq_ctl.dag_snapshots TO dq_discovery_svc;

-- red-team C3: nobody but gov_apply (and the labelled test genesis) writes rule approvals
REVOKE INSERT ON dq_ctl.registry_events FROM dq_publish_svc;
REVOKE ALL ON dq_ctl.config_artifacts, dq_ctl.config_change_requests, dq_ctl.config_cr_events FROM PUBLIC;
GRANT SELECT ON dq_ctl.config_artifacts, dq_ctl.config_change_requests, dq_ctl.config_cr_events, dq_ctl.config_effective,
  dq_ctl.config_cr_status TO dq_api, dq_registry_svc, dq_eval_svc, dq_publish_svc, dq_verify_svc, dq_auditor;
GRANT SELECT ON dq_ctl.registry_events TO dq_registry_svc;
GRANT USAGE ON SCHEMA dq_ctl TO dq_registry_svc;
REVOKE ALL ON FUNCTION dq_ctl.gov_propose(text,text,text,text,text,jsonb,jsonb,jsonb,boolean,text,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.gov_propose(text,text,text,text,text,jsonb,jsonb,jsonb,boolean,text,jsonb,jsonb) TO dq_api;
REVOKE ALL ON FUNCTION dq_ctl.lookback_reassessed(bigint,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.lookback_reassessed(bigint,text,jsonb) TO dq_api;
REVOKE INSERT ON dq_ctl.lookback_events FROM dq_api;
REVOKE ALL ON FUNCTION dq_ctl.lookback_decide(bigint,text,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.lookback_decide(bigint,text,text,text,text,jsonb) TO dq_api;
REVOKE ALL ON dq_ctl.lookback_items, dq_ctl.lookback_events, dq_ctl.org_settings FROM PUBLIC;
GRANT SELECT, INSERT ON dq_ctl.lookback_items, dq_ctl.lookback_events TO dq_registry_svc;
GRANT SELECT ON dq_ctl.lookback_items, dq_ctl.lookback_events, dq_ctl.lookback_status, dq_ctl.org_settings
  TO dq_api, dq_registry_svc, dq_auditor;
GRANT USAGE ON SEQUENCE dq_ctl.lookback_items_item_id_seq, dq_ctl.lookback_events_id_seq TO dq_registry_svc, dq_api;
GRANT SELECT ON dq_ctl.units, dq_ctl.results, dq_ctl.unit_events TO dq_registry_svc;
DROP TRIGGER IF EXISTS lookback_items_no_mut ON dq_ctl.lookback_items;
CREATE TRIGGER lookback_items_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.lookback_items FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
DROP TRIGGER IF EXISTS lookback_events_no_mut ON dq_ctl.lookback_events;
CREATE TRIGGER lookback_events_no_mut BEFORE UPDATE OR DELETE ON dq_ctl.lookback_events FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation();
ALTER TABLE dq_ctl.lookback_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lookback_scope ON dq_ctl.lookback_items;
CREATE POLICY lookback_scope ON dq_ctl.lookback_items FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS lookback_sys ON dq_ctl.lookback_items;
CREATE POLICY lookback_sys ON dq_ctl.lookback_items FOR ALL TO dq_registry_svc, dq_auditor USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS units_registry ON dq_ctl.units;
CREATE POLICY units_registry ON dq_ctl.units FOR SELECT TO dq_registry_svc USING (true);
DROP POLICY IF EXISTS results_registry ON dq_ctl.results;
CREATE POLICY results_registry ON dq_ctl.results FOR SELECT TO dq_registry_svc USING (true);
DROP POLICY IF EXISTS unit_events_registry ON dq_ctl.unit_events;
CREATE POLICY unit_events_registry ON dq_ctl.unit_events FOR SELECT TO dq_registry_svc USING (true);
REVOKE ALL ON FUNCTION dq_ctl.gov_withdraw(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.gov_withdraw(text,text) TO dq_api;
REVOKE ALL ON FUNCTION dq_ctl.gov_decide(text,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.gov_decide(text,text,text,text,jsonb) TO dq_api;
REVOKE ALL ON FUNCTION dq_ctl.gov_apply(text,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.gov_apply(text,jsonb,text,text) TO dq_registry_svc;
GRANT SELECT ON dq_ctl.roster_current TO dq_registry_svc;
"""


# ===================================================================================================================
# Part E (docs/rules/PARTE_DESIGN.md): earned autonomy, agent registry and entitlements, reconciliation (gating ABC,
# feed items, parallel run), monitoring metrics, and explanation binding. Applied after governance_ddl().
# ===================================================================================================================
TIERS = ("L1", "L2", "L3", "L4")
TIER_CEILING = {"L4": "A", "L3": "H_ON", "L2": "H_IN", "L1": "H_IN"}
ACTION_CLASSES = ("RELEASE", "POST_BOARD", "PIN_MEMORY")
AGENT_STATUSES = ("REGISTERED", "ACTIVE", "SUSPENDED")
# sensitivity order used by the lower-only proof: a CLASSIFICATION change may only move an element UP this order
CLASS_RANK = {"none": 0, "coverage": 1, "program": 1, "payment": 2, "identity": 3, "clinical": 4, "part2": 5}
MASKED_CLASSES = ("identity", "clinical", "part2")
# feed acknowledgement codes PER KIND (orchestrator decision, Part F): each kind accepts only its own codes
_DELIVERY_ACK = ("NO_ACTIVITY", "HOLIDAY", "SUBMITTER_INACTIVE", "RESUBMIT_REQUESTED")
FEED_ACK_CODES_BY_KIND = {
    "EMPTY_DELIVERY": _DELIVERY_ACK,
    "MISSING_DELIVERY": _DELIVERY_ACK,
    "UNROUTED_FILE": ("ROUTE_PROPOSED", "FILE_REJECTED", "RESUBMIT_REQUESTED"),
    "RECOVERY_DATA_LOST": ("REDELIVERY_REQUESTED", "RESUBMIT_REQUESTED"),
}
FEED_ACK_CODES = tuple(dict.fromkeys(c for v in FEED_ACK_CODES_BY_KIND.values() for c in v))   # the union
FEED_ITEM_KINDS = ("EMPTY_DELIVERY", "MISSING_DELIVERY",
                   "UNROUTED_FILE",         # Part F F-02: an inbound file no route of the FEEDS routing matches
                   "RECOVERY_DATA_LOST")    # Part F R4-1: ABC recovery refused, the batch's frozen loads are gone
DEPLOYMENTS = ("test", "demo", "pilot", "production")     # = dqagent.deploy.DEPLOYMENTS
EXPLAIN_CODES = ("TIMING", "LEGACY_DEFECT", "NEW_DEFECT", "DEFINITION", "FIXED")
STEP_UP_EXPLAIN_CODES = ("LEGACY_DEFECT", "DEFINITION")
PARALLEL_STATUSES = ("MATCH", "WITHIN", "BREAK")


# one definition (Part F P1-1): trace_ddl re-creates this view after it adds the correlation columns to veto_windows,
# because `w.*` then expands differently and CREATE OR REPLACE cannot reorder view columns
VETO_WINDOW_STATUS_VIEW = """CREATE OR REPLACE VIEW dq_ctl.veto_window_status WITH (security_invoker = true) AS
  SELECT w.*, w.opened_at + make_interval(secs => w.window_s) AS deadline,
         greatest(0, ceil(extract(epoch FROM (w.opened_at + make_interval(secs => w.window_s)) - clock_timestamp())))::int AS remaining_s,
         clock_timestamp() >= w.opened_at + make_interval(secs => w.window_s) AS elapsed,
         EXISTS (SELECT 1 FROM dq_ctl.vetoes v WHERE v.window_id = w.window_id) AS vetoed
  FROM dq_ctl.veto_windows w;
"""
VETO_WINDOW_STATUS_GRANT = """GRANT SELECT ON dq_ctl.veto_window_status
  TO dq_api, dq_eval_svc, dq_publish_svc, dq_verify_svc, dq_registry_svc, dq_auditor;"""


def _sql_list(xs) -> str:
    return ",".join(f"'{x}'" for x in xs)


def _minimum_defaults() -> str:
    """The production promotion minimums (one definition: trace_schema.DEFAULT_PROMOTION_MINIMUMS) as a SQL literal."""
    import json

    from dqagent.cp.trace_schema import DEFAULT_PROMOTION_MINIMUMS
    return "'" + json.dumps(DEFAULT_PROMOTION_MINIMUMS).replace("'", "''") + "'"


def parte_ddl() -> str:
    class_case = " ".join(f"WHEN '{k}' THEN {v}" for k, v in CLASS_RANK.items())
    ceiling_case = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in TIER_CEILING.items())
    sub = {"@TIERS@": _sql_list(TIERS), "@CLASSES@": _sql_list(ACTION_CLASSES), "@STATUSES@": _sql_list(AGENT_STATUSES),
           "@CLASS_CASE@": class_case, "@CEILING_CASE@": ceiling_case, "@ACK@": _sql_list(FEED_ACK_CODES),
           "@ACK_BY_KIND@": " ".join(f"WHEN '{k}' THEN ARRAY[{_sql_list(v)}]"
                                     for k, v in FEED_ACK_CODES_BY_KIND.items()),
           "@FEED_KINDS@": _sql_list(FEED_ITEM_KINDS), "@EXPLAIN@": _sql_list(EXPLAIN_CODES),
           "@STEPUP@": _sql_list(STEP_UP_EXPLAIN_CODES), "@PRSTATUS@": _sql_list(PARALLEL_STATUSES),
           "@DEPLOYMENTS@": _sql_list(DEPLOYMENTS), "@VETO_VIEW@": VETO_WINDOW_STATUS_VIEW,
           "@MINIMUM_DEFAULTS@": _minimum_defaults()}
    out = _PARTE_SQL
    for k, v in sub.items():
        out = out.replace(k, v)
    return out


_PARTE_SQL = r"""
-- ===== §1 / §2 helpers ===========================================================================================
CREATE OR REPLACE FUNCTION dq_ctl.tier_rank(p_tier text) RETURNS int
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT CASE p_tier WHEN 'L1' THEN 1 WHEN 'L2' THEN 2 WHEN 'L3' THEN 3 WHEN 'L4' THEN 4 ELSE NULL END $$;
CREATE OR REPLACE FUNCTION dq_ctl.tier_ceiling(p_tier text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT CASE coalesce(p_tier, 'L1') @CEILING_CASE@ ELSE 'H_IN' END $$;
CREATE OR REPLACE FUNCTION dq_ctl.class_rank(p_class text) RETURNS int
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$ SELECT CASE p_class @CLASS_CASE@ ELSE NULL END $$;
CREATE OR REPLACE FUNCTION dq_ctl.ts_or_null(p text) RETURNS timestamptz
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
BEGIN RETURN p::timestamptz; EXCEPTION WHEN others THEN RETURN NULL; END $$;

-- ===== §2 registry views (latest APPLIED version of each artifact) ==============================================
CREATE OR REPLACE VIEW dq_ctl.agents_current AS
  SELECT c.state, split_part(c.artifact_id, ':', 3) AS agent_id, c.content->>'name' AS name,
         c.content->>'version' AS version, c.content->>'status' AS status,
         ARRAY(SELECT jsonb_array_elements_text(coalesce(c.content->'seats', '[]'::jsonb))) AS seats,
         c.version AS artifact_version, c.cr_id, c.at
  FROM dq_ctl.config_effective c WHERE c.kind = 'AGENT' AND c.op = 'PUT';

CREATE OR REPLACE VIEW dq_ctl.entitlements_current AS
  SELECT c.state, split_part(c.artifact_id, ':', 3) AS agent_id, split_part(c.artifact_id, ':', 4) AS action_class,
         ARRAY(SELECT jsonb_array_elements_text(coalesce(c.content->'authorities', '[]'::jsonb))) AS authorities,
         dq_ctl.ts_or_null(c.content->>'not_before') AS not_before, dq_ctl.ts_or_null(c.content->>'not_after') AS not_after,
         coalesce(clock_timestamp() >= dq_ctl.ts_or_null(c.content->>'not_before')
                  AND clock_timestamp() < dq_ctl.ts_or_null(c.content->>'not_after'), false) AS live,
         c.version AS artifact_version, c.cr_id, c.at
  FROM dq_ctl.config_effective c WHERE c.kind = 'ENTITLEMENT' AND c.op = 'PUT';

-- one row per (state, agent, action class, authority) that HAS an entry; a missing entry means L1 (never L4)
CREATE OR REPLACE VIEW dq_ctl.autonomy_current AS
  SELECT c.state, split_part(c.artifact_id, ':', 3) AS agent_id, split_part(c.artifact_id, ':', 4) AS action_class,
         t.key AS authority, t.value #>> '{}' AS tier, dq_ctl.tier_ceiling(t.value #>> '{}') AS ceiling_mode,
         c.version AS artifact_version, c.cr_id, c.at
  FROM dq_ctl.config_effective c CROSS JOIN LATERAL jsonb_each(c.content) t
  WHERE c.kind = 'AUTONOMY' AND c.op = 'PUT';

-- the effective tier: missing = L1; across several states the LOWEST tier wins (fail closed)
CREATE OR REPLACE FUNCTION dq_ctl.autonomy_tier(p_state text, p_agent text, p_action_class text, p_authority text)
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT coalesce((SELECT a.tier FROM dq_ctl.autonomy_current a WHERE a.agent_id = p_agent
                     AND a.action_class = p_action_class AND a.authority = p_authority
                     AND (p_state IS NULL OR a.state = p_state)
                   ORDER BY coalesce(dq_ctl.tier_rank(a.tier), 1) LIMIT 1), 'L1') $$;

-- Part F P1-3 (K2a), ORCH-1: the ceiling a dataset's INTRODUCING gate currently imposes. NULL for INHERIT or no
-- row (the dataset has never been introduced by any gate); the introducing gate's release_ceiling while that gate
-- is still EFFECTIVE (a later PUT/DELETE of the same artifact_id, or a DELETE, both count as "not effective");
-- fail-closed interim 'L1' when the introducer is gone (I3: the next CR that gates the dataset re-introduces it,
-- so units are never stranded for good).
CREATE OR REPLACE FUNCTION dq_ctl.task_gate_ceiling(p_state text, p_relation text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT CASE
    WHEN i.introduced_by IS NULL THEN NULL
    WHEN NOT EXISTS (SELECT 1 FROM dq_ctl.config_effective c WHERE c.artifact_id = i.introduced_by AND c.op = 'PUT') THEN 'L1'
    WHEN (SELECT c.content->>'release_ceiling' FROM dq_ctl.config_effective c
          WHERE c.artifact_id = i.introduced_by AND c.op = 'PUT') = 'INHERIT' THEN NULL
    ELSE (SELECT c.content->>'release_ceiling' FROM dq_ctl.config_effective c WHERE c.artifact_id = i.introduced_by AND c.op = 'PUT')
  END
  FROM (SELECT introduced_by FROM dq_ctl.gated_dataset_introductions
         WHERE state = p_state AND dataset = p_relation ORDER BY version DESC LIMIT 1) i $$;

-- Part F P1-3 (K2a), ORCH-1: the per-relation tier, the lower of the 4-argument autonomy_tier (unchanged: other
-- callers -- track_record, tier_of, grants -- rely on its own 4-argument shape) and task_gate_ceiling(state,
-- relation). A NULL ceiling (INHERIT or no introduction row) means no additional ceiling applies.
CREATE OR REPLACE FUNCTION dq_ctl.autonomy_tier_for(p_state text, p_agent text, p_action_class text, p_authority text,
  p_relation text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT CASE
    WHEN dq_ctl.task_gate_ceiling(p_state, p_relation) IS NULL THEN dq_ctl.autonomy_tier(p_state, p_agent, p_action_class, p_authority)
    WHEN dq_ctl.tier_rank(dq_ctl.task_gate_ceiling(p_state, p_relation))
         <= dq_ctl.tier_rank(dq_ctl.autonomy_tier(p_state, p_agent, p_action_class, p_authority))
    THEN dq_ctl.task_gate_ceiling(p_state, p_relation)
    ELSE dq_ctl.autonomy_tier(p_state, p_agent, p_action_class, p_authority)
  END $$;

CREATE OR REPLACE FUNCTION dq_ctl.entitled(p_agent text, p_action_class text, p_authority text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM dq_ctl.entitlements_current e WHERE e.agent_id = p_agent
                 AND e.action_class = p_action_class AND p_authority = ANY (e.authorities) AND e.live) $$;

-- why an agent seat may NOT act (NULL = it may): registered, ACTIVE, the seat bound to the agent, live entitlement
CREATE OR REPLACE FUNCTION dq_ctl.agent_refusal(p_agent text, p_seat text, p_action_class text, p_authority text)
RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF p_agent IS NULL OR NOT EXISTS (SELECT 1 FROM dq_ctl.agents_current g WHERE g.agent_id = p_agent) THEN
    RETURN format('agent %s is not registered', coalesce(p_agent, '(none)'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM dq_ctl.agents_current g WHERE g.agent_id = p_agent AND g.status = 'ACTIVE') THEN
    RETURN format('agent %s is not ACTIVE', p_agent);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM dq_ctl.agents_current g WHERE g.agent_id = p_agent AND g.status = 'ACTIVE'
                 AND p_seat = ANY (g.seats)) THEN
    RETURN format('seat %s is not bound to agent %s', p_seat, p_agent);
  END IF;
  IF NOT dq_ctl.entitled(p_agent, p_action_class, p_authority) THEN
    RETURN format('agent %s holds no live %s entitlement for %s', p_agent, p_action_class, p_authority);
  END IF;
  RETURN NULL;
END $$;

-- Part F P1-3 (K2a): a recursive guard against a literal secret value anywhere in a PIPELINE / TASK_GATE item's
-- content (a reference such as a secret manager URI is not a literal value; this refuses only a bare string under
-- a key that LOOKS like a credential, fail closed). No existing helper does this; small and new, per-key, recursive
-- because PIPELINE's content nests (scope.manifest_path).
CREATE OR REPLACE FUNCTION dq_ctl._no_literal_secrets(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE k text; v jsonb; e jsonb;
BEGIN
  IF jsonb_typeof(p) = 'object' THEN
    FOR k, v IN SELECT * FROM jsonb_each(p) LOOP
      IF k ~* '(token|password|secret|key)' AND jsonb_typeof(v) = 'string' THEN RETURN false; END IF;
      IF NOT dq_ctl._no_literal_secrets(v) THEN RETURN false; END IF;
    END LOOP;
  ELSIF jsonb_typeof(p) = 'array' THEN
    FOR e IN SELECT * FROM jsonb_array_elements(p) LOOP
      IF NOT dq_ctl._no_literal_secrets(e) THEN RETURN false; END IF;
    END LOOP;
  END IF;
  RETURN true;
END $$;

-- ===== §1.2 / §2 / §5: the lower-only proof (C4) ===============================================================
-- Refuses malformed AUTONOMY / AGENT / ENTITLEMENT / CLASSIFICATION / CDE / MONITOR / PIPELINE / TASK_GATE items
-- (fail closed). p_origin (Part F P1-3, K2a-1): the change request's OWN stored origin (never caller-asserted
-- elsewhere), needed only to refuse a TASK_GATE release_ceiling of INHERIT outside a GENESIS proposal.
DROP FUNCTION IF EXISTS dq_ctl.gov_item_valid(text,jsonb);
CREATE OR REPLACE FUNCTION dq_ctl.gov_item_valid(p_state text, p_item jsonb, p_origin text) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE k text := p_item->>'kind'; aid text := p_item->>'artifact_id'; op text := p_item->>'op';
        c jsonb := p_item->'content'; ok boolean := true; st text := quote_ident(p_state);
BEGIN
  IF k NOT IN ('AUTONOMY','AGENT','ENTITLEMENT','CLASSIFICATION','CDE','MONITOR','PIPELINE','TASK_GATE') THEN RETURN; END IF;
  IF op NOT IN ('PUT','DELETE') OR left(aid, length(p_state) + 1) <> p_state || ':' THEN ok := false; END IF;
  aid := substr(aid, length(p_state) + 2);
  IF k = 'PIPELINE' THEN
    ok := coalesce(ok AND aid ~ '^pipeline:[a-z0-9_-]{1,48}$'
          AND (op = 'DELETE' OR (jsonb_typeof(c) = 'object' AND dq_ctl._no_literal_secrets(c)
               AND c->>'adapter' = 'dbt_files'
               AND jsonb_typeof(c->'scope') = 'object' AND jsonb_typeof(c->'scope'->'manifest_path') = 'string'
               AND length(c->'scope'->>'manifest_path') > 0
               AND jsonb_typeof(c->'platform') = 'string' AND length(c->>'platform') > 0
               AND jsonb_typeof(coalesce(c->'domains', '[]'::jsonb)) = 'array'
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(c->'domains', '[]'::jsonb)) x
                                WHERE jsonb_typeof(x) <> 'string' OR length(x #>> '{}') = 0)
               AND jsonb_typeof(coalesce(c->'layer_map', '{}'::jsonb)) = 'object'
               AND c->>'run_on' IN ('night_end', 'manual'))), false);
  ELSIF k = 'TASK_GATE' THEN
    ok := coalesce(ok AND aid ~ '^taskgate:[a-z0-9_-]{1,48}:[a-z0-9_-]{1,48}$'
          AND (op = 'DELETE' OR (jsonb_typeof(c) = 'object' AND dq_ctl._no_literal_secrets(c)
               AND jsonb_typeof(c->'task_id') = 'string' AND length(c->>'task_id') > 0
               AND jsonb_typeof(c->'datasets') = 'array' AND jsonb_array_length(c->'datasets') >= 1
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(c->'datasets') x
                                WHERE jsonb_typeof(x) <> 'string' OR length(x #>> '{}') = 0)
               AND c->>'mode' IN ('block', 'hold_until_terminal', 'advisory')
               AND c->>'correlate' = 'LATEST_SEALED'
               AND dq_ctl.cadence_seconds(c->>'max_wait') IS NOT NULL
               AND c->>'on_timeout' IN ('HOLD', 'ESCALATE', 'PASS_ADVISORY')
               AND c->>'release_ceiling' IN ('L1', 'L2', 'L3', 'L4', 'INHERIT')
               AND (c->>'release_ceiling' <> 'INHERIT' OR p_origin = 'GENESIS')
               AND jsonb_typeof(coalesce(c->'reintroduce', '[]'::jsonb)) = 'array'
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(c->'reintroduce', '[]'::jsonb)) x
                                WHERE jsonb_typeof(x) <> 'string' OR length(x #>> '{}') = 0))), false);
    -- K9 (ORCH-1 step b, K7_K8_K9_DESIGN.md §3.3 point 1, before-build red team CRITICAL finding #1 FIXED): a
    -- release_ceiling CHANGE (not every TASK_GATE edit) on a gate that is not the introducing gate for ANY of its
    -- own already-introduced datasets is refused ("ceiling is inert" -- raising/lowering it there has no effect,
    -- since `task_gate_ceiling` only ever reads the ceiling of the dataset's actual `introduced_by` gate). A
    -- dataset with NO `gated_dataset_introductions` row at all is exempt (not evidence either way): introduction
    -- happens inside `gov_apply` -> `_introduce_datasets`, strictly AFTER this propose-time check, so a brand-new
    -- dataset genuinely has not been decided yet. Reuses the same NULL-safe shape as `task_gate_ceiling` (:738,
    -- `WHEN i.introduced_by IS NULL THEN NULL`) rather than a new ad hoc pattern. Per-gate framing: the whole
    -- ceiling change is refused only if the gate introduced NONE of its own already-introduced datasets; if every
    -- listed dataset has zero rows (a brand-new gate's first wiring), nothing here fires at all.
    IF ok AND op = 'PUT' THEN
      DECLARE
        old_c jsonb; has_any boolean; is_introducer boolean; other_introducer text;
      BEGIN
        SELECT ce.content INTO old_c FROM dq_ctl.config_effective ce
         WHERE ce.artifact_id = p_item->>'artifact_id' AND ce.op = 'PUT';
        IF old_c IS NOT NULL AND old_c->>'release_ceiling' IS DISTINCT FROM c->>'release_ceiling' THEN
          SELECT bool_or(i.introduced_by IS NOT NULL), bool_or(i.introduced_by = p_item->>'artifact_id')
            INTO has_any, is_introducer
            FROM jsonb_array_elements_text(c->'datasets') ds
            LEFT JOIN LATERAL (SELECT introduced_by FROM dq_ctl.gated_dataset_introductions
                               WHERE state = p_state AND dataset = ds ORDER BY version DESC LIMIT 1) i ON true;
          IF coalesce(has_any, false) AND NOT coalesce(is_introducer, false) THEN
            SELECT i.introduced_by INTO other_introducer
              FROM jsonb_array_elements_text(c->'datasets') ds
              LEFT JOIN LATERAL (SELECT introduced_by FROM dq_ctl.gated_dataset_introductions
                                 WHERE state = p_state AND dataset = ds ORDER BY version DESC LIMIT 1) i ON true
              WHERE i.introduced_by IS NOT NULL LIMIT 1;
            RAISE EXCEPTION 'ceiling is inert: datasets are gated by %', other_introducer USING ERRCODE = 'check_violation';
          END IF;
        END IF;
      END;
    END IF;
  ELSIF k = 'AUTONOMY' THEN
    ok := coalesce(ok AND aid ~ '^autonomy:[a-z0-9-]{1,48}:(RELEASE|POST_BOARD|PIN_MEMORY)$'
          AND (op = 'DELETE' OR (jsonb_typeof(c) = 'object' AND NOT EXISTS (
                 SELECT 1 FROM jsonb_each(c) t WHERE jsonb_typeof(t.value) <> 'string'
                    OR dq_ctl.tier_rank(t.value #>> '{}') IS NULL OR t.key !~ '^AUTH_[A-Z0-9_]{1,40}$'))), false);
  ELSIF k = 'AGENT' THEN
    ok := coalesce(ok AND aid ~ '^agent:[a-z0-9-]{1,48}$'
          AND (op = 'DELETE' OR (jsonb_typeof(c) = 'object' AND c->>'status' IN (@STATUSES@)
               AND jsonb_typeof(coalesce(c->'seats', '[]'::jsonb)) = 'array'
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(c->'seats', '[]'::jsonb)) x
                                WHERE jsonb_typeof(x) <> 'string' OR (x #>> '{}') !~ '^[A-Z_]{1,32}$'))), false);
  ELSIF k = 'ENTITLEMENT' THEN
    ok := coalesce(ok AND aid ~ '^entitlement:[a-z0-9-]{1,48}:(RELEASE|POST_BOARD|PIN_MEMORY)$'
          AND (op = 'DELETE' OR (jsonb_typeof(c) = 'object' AND jsonb_typeof(c->'authorities') = 'array'
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(c->'authorities') x
                                WHERE jsonb_typeof(x) <> 'string' OR (x #>> '{}') !~ '^AUTH_[A-Z0-9_]{1,40}$')
               AND dq_ctl.ts_or_null(c->>'not_before') IS NOT NULL AND dq_ctl.ts_or_null(c->>'not_after') IS NOT NULL
               AND dq_ctl.ts_or_null(c->>'not_before') < dq_ctl.ts_or_null(c->>'not_after'))), false);
  ELSIF k = 'CLASSIFICATION' THEN
    ok := coalesce(ok AND aid = 'classification' AND (op = 'DELETE' OR (jsonb_typeof(c) = 'object' AND NOT EXISTS (
            SELECT 1 FROM jsonb_each(c) t WHERE jsonb_typeof(t.value) <> 'string'
               OR dq_ctl.class_rank(t.value #>> '{}') IS NULL))), false);
  ELSIF k = 'CDE' THEN
    ok := coalesce(ok AND aid = 'cde' AND (op = 'DELETE' OR jsonb_typeof(c) = 'object'), false);
  ELSIF k = 'MONITOR' THEN
    ok := coalesce(ok AND aid = 'monitor' AND (op = 'DELETE' OR jsonb_typeof(c) = 'object'), false);
  END IF;
  IF NOT ok THEN
    RAISE EXCEPTION 'malformed % item %', k, p_item->>'artifact_id' USING ERRCODE = 'check_violation';
  END IF;
END $$;

-- TRUE only when SQL proves the item LOWERS what an agent may do (or tightens a classification):
--   AUTONOMY        every authority's new tier rank <= its old rank (a missing entry is L1); DELETE = all to L1
--   ENTITLEMENT     authorities a subset, not_after earlier or equal, not_before later or equal; DELETE = revoke
--   AGENT           same name / version / other fields, seats a subset, status unchanged or ACTIVE -> SUSPENDED |
--                   REGISTERED (or between those two)
--   CLASSIFICATION  every element keeps its class or moves to a MORE sensitive one (a downgrade needs two)
-- Anything else on these kinds, including a brand-new AUTONOMY / ENTITLEMENT / AGENT, is NOT lower-only.
-- Other kinds return TRUE here: their classification stays with the server classifier.
-- Part F red team R5 (HIGH): a FEEDS change is lower-only ONLY when every key outside the tightening set is unchanged
-- (routing included) and each tightening key moves only in its tightening direction:
--   part2_programs / part2_relations only grow; not_delivered_elements only shrinks; the feed set is unchanged;
--   per feed: part2_program never leaves true (and changes only to true), delivered stays or goes false -> true,
--   empty_ok stays or goes true -> false, cadence stays or its `every` gets SHORTER (other cadence keys unchanged).
-- Free text (a feed's reason / note) is outside the set: changing it needs two people too (fail closed).
CREATE OR REPLACE FUNCTION dq_ctl.cadence_seconds(p text) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE m text[];
BEGIN
  IF p ~ '^P[0-9]+M$' THEN RETURN substring(p FROM 2 FOR length(p) - 2)::bigint * 31 * 86400; END IF;  -- = runtime
  m := regexp_match(coalesce(p, ''), '^P(?:([0-9]+)W)?(?:([0-9]+)D)?(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+)S)?)?$');
  IF m IS NULL OR p ~ 'T$' OR (m[1] IS NULL AND m[2] IS NULL AND m[3] IS NULL AND m[4] IS NULL AND m[5] IS NULL) THEN
    RETURN NULL;
  END IF;
  RETURN (((coalesce(m[1],'0')::bigint * 7 + coalesce(m[2],'0')::bigint) * 24 + coalesce(m[3],'0')::bigint) * 60
          + coalesce(m[4],'0')::bigint) * 60 + coalesce(m[5],'0')::bigint;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.feeds_lower_only(o jsonb, n jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE top text[] := ARRAY['feeds','not_delivered_elements','part2_programs','part2_relations'];
        per text[] := ARRAY['part2_program','delivered','empty_ok','cadence'];
        k text; fo jsonb; fn jsonb; so bigint; sn bigint;
BEGIN
  IF o IS NULL OR n IS NULL OR jsonb_typeof(o) <> 'object' OR jsonb_typeof(n) <> 'object' THEN RETURN false; END IF;
  IF (n - top) IS DISTINCT FROM (o - top) THEN RETURN false; END IF;               -- routing and every other key
  IF NOT (coalesce(n->'part2_programs', '[]') @> coalesce(o->'part2_programs', '[]')) THEN RETURN false; END IF;
  IF NOT (coalesce(n->'part2_relations', '[]') @> coalesce(o->'part2_relations', '[]')) THEN RETURN false; END IF;
  IF NOT (coalesce(n->'not_delivered_elements', '[]') <@ coalesce(o->'not_delivered_elements', '[]')) THEN
    RETURN false;
  END IF;
  IF (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(coalesce(o->'feeds', '{}')) x)
     IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(coalesce(n->'feeds', '{}')) x) THEN
    RETURN false;
  END IF;
  FOR k IN SELECT jsonb_object_keys(coalesce(o->'feeds', '{}')) LOOP
    fo := o->'feeds'->k; fn := n->'feeds'->k;
    IF jsonb_typeof(fo) <> 'object' OR jsonb_typeof(fn) <> 'object' THEN
      IF fo IS DISTINCT FROM fn THEN RETURN false; END IF;
      CONTINUE;
    END IF;
    IF (fn - per) IS DISTINCT FROM (fo - per) THEN RETURN false; END IF;
    IF (fn->'part2_program') IS DISTINCT FROM (fo->'part2_program')
       AND (fn->'part2_program') IS DISTINCT FROM 'true'::jsonb THEN RETURN false; END IF;
    IF (fn->'delivered') IS DISTINCT FROM (fo->'delivered')            -- NULL-safe: an absent key is not a direction
       AND NOT ((fn->'delivered') IS NOT DISTINCT FROM 'true'::jsonb
                AND (fo->'delivered') IS NOT DISTINCT FROM 'false'::jsonb) THEN
      RETURN false;
    END IF;
    IF (fn->'empty_ok') IS DISTINCT FROM (fo->'empty_ok')
       AND NOT ((fo->'empty_ok') IS NOT DISTINCT FROM 'true'::jsonb
                AND (fn->'empty_ok') IS NOT DISTINCT FROM 'false'::jsonb) THEN RETURN false; END IF;
    IF (fn->'cadence') IS DISTINCT FROM (fo->'cadence') THEN
      IF jsonb_typeof(fo->'cadence') IS DISTINCT FROM 'object' OR jsonb_typeof(fn->'cadence') IS DISTINCT FROM 'object'
         OR ((fn->'cadence') - 'every') IS DISTINCT FROM ((fo->'cadence') - 'every') THEN RETURN false; END IF;
      so := dq_ctl.cadence_seconds(fo->'cadence'->>'every'); sn := dq_ctl.cadence_seconds(fn->'cadence'->>'every');
      IF so IS NULL OR sn IS NULL OR sn >= so THEN RETURN false; END IF;
    END IF;
  END LOOP;
  RETURN true;
END $$;

-- "lower-only" for the promotion minimums means STRICTER: every at-least count stays or rises, every at-most bound
-- stays or falls, no tier or key disappears. No minimums before: any new set is stricter than the defaults only if it
-- is compared with them, so a first set is compared with the production defaults (trace_schema).
CREATE OR REPLACE FUNCTION dq_ctl.minimums_lower_only(p_old jsonb, p_new jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE tier text; key text; o jsonb; n jsonb;
BEGIN
  IF p_old IS NULL AND p_new IS NULL THEN RETURN true; END IF;
  IF p_new IS NULL THEN RETURN false; END IF;
  p_old := coalesce(p_old, @MINIMUM_DEFAULTS@::jsonb);
  FOR tier IN SELECT jsonb_object_keys(p_old) LOOP
    CONTINUE WHEN jsonb_typeof(p_old->tier) <> 'object';
    o := p_old->tier; n := p_new->tier;
    IF n IS NULL OR jsonb_typeof(n) <> 'object' THEN RETURN false; END IF;
    FOR key IN SELECT jsonb_object_keys(o) LOOP
      IF NOT (n ? key) OR jsonb_typeof(n->key) <> 'number' THEN RETURN false; END IF;
      IF key IN ('human_decisions','human_releases','released_after_window') THEN
        IF (n->>key)::numeric < (o->>key)::numeric THEN RETURN false; END IF;
      ELSIF (n->>key)::numeric > (o->>key)::numeric THEN RETURN false;
      END IF;
    END LOOP;
  END LOOP;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.gov_item_lower_only(p_state text, p_item jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE k text := p_item->>'kind'; op text := p_item->>'op'; nw jsonb := p_item->'content'; old jsonb;
BEGIN
  IF k = 'FEEDS' THEN
    -- Part F F-02 / red team I6: the routing block decides which pipeline, parser and relations (so which rules)
    -- an inbound file reaches. Any change to it, a new FEEDS artifact or a deletion is NOT lower-only: the
    -- DATABASE requires two approvers whatever the caller's loosening flag says.
    SELECT c.content INTO old FROM dq_ctl.config_effective c
     WHERE c.artifact_id = p_item->>'artifact_id' AND c.op = 'PUT';
    RETURN old IS NOT NULL AND op = 'PUT' AND dq_ctl.feeds_lower_only(old, nw);
  END IF;
  IF k = 'POLICY' THEN
    -- Part F R4-5: promotion_minimums may be RAISED by one approver; lowering (a smaller at-least, a larger
    -- at-most, a removed tier or key, a deleted policy) needs two. Other POLICY keys: as before (lower-only).
    SELECT c.content INTO old FROM dq_ctl.config_effective c
     WHERE c.artifact_id = p_item->>'artifact_id' AND c.op = 'PUT';
    RETURN dq_ctl.minimums_lower_only(old->'promotion_minimums', CASE WHEN op = 'DELETE' THEN NULL
                                      ELSE nw->'promotion_minimums' END);
  END IF;
  -- Part F P1-3 (K2a): HARNESS is structural (dependency changes affect release ordering across pipelines) and a
  -- PIPELINE item is never lower-only, whatever it changes -- both always need two approvers.
  -- K2a-4 (pre-existing defect, found and fixed here, not introduced by K2): HARNESS previously fell through to
  -- the function's final `RETURN true`, the OPPOSITE of this rule, so a HARNESS change incorrectly needed only one
  -- approver. Fixed on BOTH sides: here, and in registry.lower_only's Python mirror.
  IF k = 'HARNESS' THEN RETURN false; END IF;
  IF k = 'PIPELINE' THEN RETURN false; END IF;
  IF k = 'EVAL_CASE' THEN RETURN false; END IF;
  IF k = 'TASK_GATE' THEN
    IF op = 'DELETE' THEN RETURN false; END IF;              -- removing a gate is never lower-only
    SELECT c.content INTO old FROM dq_ctl.config_effective c
     WHERE c.artifact_id = p_item->>'artifact_id' AND c.op = 'PUT';
    IF old IS NULL THEN RETURN false; END IF;                -- a brand-new gate always needs two
    RETURN coalesce(
      (nw->'datasets') @> (old->'datasets')
      AND (nw->>'release_ceiling' = old->>'release_ceiling'
           OR (dq_ctl.tier_rank(nw->>'release_ceiling') IS NOT NULL AND dq_ctl.tier_rank(old->>'release_ceiling') IS NOT NULL
               AND dq_ctl.tier_rank(nw->>'release_ceiling') <= dq_ctl.tier_rank(old->>'release_ceiling')))
      AND dq_ctl.cadence_seconds(nw->>'max_wait') IS NOT NULL AND dq_ctl.cadence_seconds(old->>'max_wait') IS NOT NULL
      AND dq_ctl.cadence_seconds(nw->>'max_wait') <= dq_ctl.cadence_seconds(old->>'max_wait')
      AND (nw->>'on_timeout' = old->>'on_timeout' OR nw->>'on_timeout' = 'HOLD')
      AND nw->>'mode' = old->>'mode'                                            -- ADJ-2: mode is unchanged
      AND nw->>'correlate' = old->>'correlate'
      AND coalesce(jsonb_array_length(coalesce(nw->'reintroduce', '[]'::jsonb)), 0) = 0, false);
  END IF;
  IF k NOT IN ('AUTONOMY','ENTITLEMENT','AGENT','CLASSIFICATION') THEN RETURN true; END IF;
  SELECT c.content INTO old FROM dq_ctl.config_effective c
   WHERE c.artifact_id = p_item->>'artifact_id' AND c.op = 'PUT';
  IF k = 'AUTONOMY' THEN
    IF old IS NULL THEN RETURN false; END IF;
    IF op = 'DELETE' THEN RETURN true; END IF;
    RETURN NOT EXISTS (SELECT 1 FROM jsonb_each_text(nw) t
                       WHERE dq_ctl.tier_rank(t.value) IS NULL
                          OR dq_ctl.tier_rank(t.value) > coalesce(dq_ctl.tier_rank(old->>t.key), 1));
  ELSIF k = 'ENTITLEMENT' THEN
    IF old IS NULL THEN RETURN false; END IF;
    IF op = 'DELETE' THEN RETURN true; END IF;
    RETURN coalesce((nw->'authorities') <@ (old->'authorities')
       AND dq_ctl.ts_or_null(nw->>'not_after') <= dq_ctl.ts_or_null(old->>'not_after')
       AND dq_ctl.ts_or_null(nw->>'not_before') >= dq_ctl.ts_or_null(old->>'not_before')
       AND (nw - 'authorities' - 'not_before' - 'not_after') = (old - 'authorities' - 'not_before' - 'not_after'), false);
  ELSIF k = 'AGENT' THEN
    IF old IS NULL OR op = 'DELETE' THEN RETURN false; END IF;
    RETURN coalesce((nw - 'status' - 'seats') = (old - 'status' - 'seats')
       AND coalesce(nw->'seats', '[]'::jsonb) <@ coalesce(old->'seats', '[]'::jsonb)
       AND (nw->>'status' = old->>'status' OR nw->>'status' IN ('SUSPENDED','REGISTERED')), false);
  ELSE  -- CLASSIFICATION
    IF op = 'DELETE' THEN RETURN old IS NULL; END IF;
    RETURN NOT EXISTS (SELECT 1 FROM jsonb_each_text(coalesce(old, '{}'::jsonb)) o
                       WHERE NOT (nw ? o.key)
                          OR (nw->>o.key <> o.value AND coalesce(dq_ctl.class_rank(nw->>o.key), -1)
                                                        <= coalesce(dq_ctl.class_rank(o.value), 99)))
       AND NOT EXISTS (SELECT 1 FROM jsonb_each_text(nw) n WHERE dq_ctl.class_rank(n.value) IS NULL);
  END IF;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.gov_items_lower_only(p_state text, p_items jsonb) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT coalesce(bool_and(dq_ctl.gov_item_lower_only(p_state, x)), true) FROM jsonb_array_elements(p_items) x $$;

-- ===== §1.2 demotion (automatic, lower-only) ===================================================================
-- INTERNAL (owner only). p_strict: refuse anything that is not a strict lowering (the public entry point);
-- otherwise silently skip when the tier is already at or below p_to_tier (the veto / look-back triggers).
CREATE OR REPLACE FUNCTION dq_ctl._autonomy_demote(p_state text, p_agent text, p_action_class text, p_authority text,
  p_to_tier text, p_reason_code text, p_ref text, p_strict boolean)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE a record; cur text; nc jsonb; cr text; it jsonb; n int := 0; refs text[];
BEGIN
  IF dq_ctl.tier_rank(p_to_tier) IS NULL OR p_reason_code !~ '^[A-Z_]{3,32}$' THEN
    RAISE EXCEPTION 'demote to L1..L3 with a reason code' USING ERRCODE = 'check_violation';
  END IF;
  refs := CASE WHEN p_ref IS NOT NULL AND dq_ctl.refs_ok(ARRAY[p_ref]) THEN ARRAY[p_ref] ELSE '{}'::text[] END;
  FOR a IN SELECT c.* FROM dq_ctl.config_effective c
            WHERE c.kind = 'AUTONOMY' AND c.op = 'PUT' AND split_part(c.artifact_id, ':', 3) = p_agent
              AND split_part(c.artifact_id, ':', 4) = p_action_class AND (p_state IS NULL OR c.state = p_state)
            ORDER BY c.artifact_id LOOP
    PERFORM pg_advisory_xact_lock(hashtext('dq-config:' || a.state));
    cur := coalesce(a.content->>p_authority, 'L1');
    IF dq_ctl.tier_rank(p_to_tier) >= dq_ctl.tier_rank(cur) THEN
      IF p_strict THEN
        RAISE EXCEPTION 'demotion is lower-only: % is at %, not above %', p_authority, cur, p_to_tier
          USING ERRCODE = 'check_violation';
      END IF;
      CONTINUE;
    END IF;
    nc := a.content || jsonb_build_object(p_authority, p_to_tier);
    cr := 'DEM-' || substr(md5(a.artifact_id || '|' || p_authority || '|' || clock_timestamp()::text || random()::text), 1, 12);
    it := jsonb_build_object('artifact_id', a.artifact_id, 'kind', 'AUTONOMY', 'op', 'PUT', 'content', nc,
                             'content_hash', encode(sha256(convert_to(nc::text, 'UTF8')), 'hex'));
    INSERT INTO dq_ctl.config_change_requests(cr_id,state,author,origin,rationale,items,impact,base,loosening,
        approvals_required,signed_hash,signed_rules,origin_detail)
      VALUES (cr, a.state, 'SYSTEM:DEMOTION', 'DEMOTION', 'Automatic demotion (' || p_reason_code || '), lower-only',
              jsonb_build_array(it), jsonb_build_object('rules', '[]'::jsonb, 'demotion', jsonb_build_object(
                 'agent_id', p_agent, 'action_class', p_action_class, 'authority', p_authority, 'from_tier', cur,
                 'to_tier', p_to_tier)),
              jsonb_build_object('versions', jsonb_build_object(a.artifact_id, a.version)), false, 1,
              encode(sha256(convert_to(it::text, 'UTF8')), 'hex'), '{}'::jsonb,
              jsonb_build_object('reason_code', p_reason_code, 'ref', p_ref, 'caller', session_user));
    INSERT INTO dq_ctl.config_artifacts(artifact_id,version,state,kind,op,content,content_hash,cr_id)
      VALUES (a.artifact_id, a.version + 1, a.state, 'AUTONOMY', 'PUT', nc, it->>'content_hash', cr);
    INSERT INTO dq_ctl.config_cr_events(cr_id,event,detail) VALUES (cr,'PROPOSED',jsonb_build_object('origin','DEMOTION'));
    INSERT INTO dq_ctl.config_cr_events(cr_id,event) VALUES (cr,'APPLIED');
    PERFORM dq_ctl.append_audit('AUTONOMY_DEMOTED', encode(sha256(convert_to(cr || '|' || a.artifact_id || '|' ||
                                p_authority || '|' || cur || '>' || p_to_tier, 'UTF8')), 'hex'));
    PERFORM dq_ctl._bb_append(p_agent, 'REGISTRY', NULL, NULL, p_authority, 'NOTE', 'AGENT', p_agent, 'AUTONOMY_DEMOTED',
            jsonb_build_object('agent_id', p_agent, 'action_class', p_action_class, 'authority', p_authority,
                               'from_tier', cur, 'to_tier', p_to_tier, 'reason_code', p_reason_code, 'cr_id', cr),
            refs || ARRAY['cr:' || cr]);
    n := n + 1;
  END LOOP;
  IF p_strict AND n = 0 THEN
    RAISE EXCEPTION 'demotion is lower-only: no % tier above % for % / %', p_action_class, p_to_tier, p_agent, p_authority
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN n;
END $$;

-- PUBLIC entry point: the registry service only. Returns the number of state artifacts demoted.
CREATE OR REPLACE FUNCTION dq_ctl.autonomy_demote(p_agent text, p_action_class text, p_authority text, p_to_tier text,
  p_reason_code text, p_ref text, p_state text DEFAULT NULL)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF session_user <> 'dq_registry_svc' THEN
    RAISE EXCEPTION 'only the registry service demotes' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN dq_ctl._autonomy_demote(p_state, p_agent, p_action_class, p_authority, p_to_tier, p_reason_code, p_ref, true);
END $$;

-- ===== §1.3 eval attestations (I7; two-person after the post-build red team, E4) ================================
-- A governance person PROPOSES an attestation (eval_attest); a SECOND governance person, of the approver
-- organisation when the tenant names one, CONFIRMS it (eval_attest_confirm). Only a CONFIRMED attestation moves the
-- track record's "since": one person can no longer reset the evidence promotion approvers read.
CREATE TABLE IF NOT EXISTS dq_ctl.eval_attestations(
  id bigserial PRIMARY KEY, label text NOT NULL CHECK (label ~ '^[A-Za-z0-9_.:@/+-]{1,120}$'),
  code_sha256 text NOT NULL CHECK (code_sha256 ~ '^[0-9a-f]{64}$'), tests int NOT NULL CHECK (tests >= 0),
  failures int NOT NULL CHECK (failures >= 0 AND failures <= tests),
  junit_sha256 text NOT NULL CHECK (junit_sha256 ~ '^[0-9a-f]{64}$'), person_id text NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS dq_ctl.eval_attestation_confirmations(
  id bigserial PRIMARY KEY, attestation_id bigint NOT NULL UNIQUE REFERENCES dq_ctl.eval_attestations(id),
  person_id text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());
-- the database refuses a confirmation by the proposer, whatever the caller (defence in depth under the function)
CREATE OR REPLACE FUNCTION dq_ctl.eval_confirm_two_person() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM dq_ctl.eval_attestations a WHERE a.id = NEW.attestation_id AND a.person_id = NEW.person_id) THEN
    RAISE EXCEPTION 'an eval attestation is confirmed by a second person, not its proposer' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS eval_confirm_two_person ON dq_ctl.eval_attestation_confirmations;
CREATE TRIGGER eval_confirm_two_person BEFORE INSERT ON dq_ctl.eval_attestation_confirmations
  FOR EACH ROW EXECUTE FUNCTION dq_ctl.eval_confirm_two_person();
-- Part F R4-5: eval_attestations_confirmed, the attestation function (5 arguments, it references recorded eval runs)
-- and the track record are defined ONCE, in trace_schema.evidence_ddl (after eval_runs).

CREATE OR REPLACE FUNCTION dq_ctl._governance_person(p_person text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM dq_ctl.roster_current g WHERE g.person_id = p_person AND g.duty = 'governance') $$;

CREATE OR REPLACE FUNCTION dq_ctl.eval_attest_confirm(p_id bigint, p_person text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE a record; i bigint; ao text;
BEGIN
  IF session_user <> 'dq_api' OR p_person IS NULL THEN
    RAISE EXCEPTION 'eval attestations are confirmed by a named person through the api' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO a FROM dq_ctl.eval_attestations WHERE id = p_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown eval attestation' USING ERRCODE = 'no_data_found'; END IF;
  IF a.person_id = p_person THEN
    RAISE EXCEPTION 'an eval attestation is confirmed by a second person, not its proposer' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT dq_ctl._governance_person(p_person) THEN
    RAISE EXCEPTION 'eval attestations are confirmed by a governance person' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT value INTO ao FROM dq_ctl.org_settings WHERE key = 'approver_org';
  IF ao IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dq_ctl.roster_current g WHERE g.person_id = p_person
                                    AND g.duty = 'governance' AND g.org = ao) THEN
    RAISE EXCEPTION 'eval attestations are confirmed by the % organisation', ao USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.eval_attestation_confirmations WHERE attestation_id = p_id) THEN
    RAISE EXCEPTION 'eval attestation already confirmed' USING ERRCODE = 'unique_violation';
  END IF;
  INSERT INTO dq_ctl.eval_attestation_confirmations(attestation_id, person_id) VALUES (p_id, p_person) RETURNING id INTO i;
  PERFORM dq_ctl.append_audit('EVAL_CONFIRMED', encode(sha256(convert_to(p_id || '|' || a.code_sha256 || '|' || p_person, 'UTF8')), 'hex'));
  RETURN i;
END $$;

-- ===== §1.4 veto windows and vetoes (C2, C3, N1) =================================================================
CREATE TABLE IF NOT EXISTS dq_ctl.veto_windows(
  window_id bigserial PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  eval_version int NOT NULL, opened_at timestamptz NOT NULL DEFAULT now(),
  window_s int NOT NULL CHECK (window_s > 0 AND window_s <= 604800), policy_hash text NOT NULL,
  state text, UNIQUE (unit_key, eval_version));
-- E8: the state whose RELEASE tier opened the window; a veto demotes that state's tier only (NULL: the evaluator
-- read the lowest tier across states, so the veto lowers it in every state - the conservative direction)
ALTER TABLE dq_ctl.veto_windows ADD COLUMN IF NOT EXISTS state text;
-- the DATABASE clock opens the window: a writer cannot back-date it
CREATE OR REPLACE FUNCTION dq_ctl.veto_window_clock() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
BEGIN NEW.opened_at := clock_timestamp(); RETURN NEW; END $$;
DROP TRIGGER IF EXISTS veto_windows_clock ON dq_ctl.veto_windows;
CREATE TRIGGER veto_windows_clock BEFORE INSERT ON dq_ctl.veto_windows FOR EACH ROW EXECUTE FUNCTION dq_ctl.veto_window_clock();

CREATE TABLE IF NOT EXISTS dq_ctl.vetoes(
  veto_id bigserial PRIMARY KEY, unit_key text NOT NULL REFERENCES dq_ctl.units(unit_key),
  window_id bigint NOT NULL REFERENCES dq_ctl.veto_windows(window_id), state_version int NOT NULL,
  person_id text NOT NULL, authority text NOT NULL, reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z_]{3,32}$'),
  jti text UNIQUE, explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref)),
  at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE (unit_key, state_version));

@VETO_VIEW@

-- a person vetoes an EVALUATING unit that has a veto window: EVALUATING -> HELD via VETO, and the RELEASE tier of
-- every authority of the unit drops to L2 in the window's state (automatic, lower-only; E8). The window's deadline
-- is NOT a cut-off for the veto: a veto after the deadline is still accepted (and still demotes) for as long as the
-- unit has not been released, because the publisher releases only on its next cycle. That is the safe direction (a
-- late veto only holds more). Released units are past veto (409).
CREATE OR REPLACE FUNCTION dq_ctl.veto_unit(p_unit text, p_expected_version int, p_person text, p_reason_code text,
  p_jti text DEFAULT NULL, p_explanation jsonb DEFAULT NULL)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE u record; w record; nv int; auth text;
BEGIN
  IF session_user <> 'dq_api' OR p_person IS NULL THEN
    RAISE EXCEPTION 'a veto is recorded by a named person through the api' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO u FROM dq_ctl.units WHERE unit_key = p_unit FOR UPDATE;
  IF NOT FOUND OR NOT dq_ctl.unit_in_scope(u.authorities) THEN
    RAISE EXCEPTION 'unknown unit' USING ERRCODE = 'no_data_found';
  END IF;
  IF u.state <> 'EVALUATING' THEN
    RAISE EXCEPTION 'unit is % (a veto applies only before release)', u.state USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT * INTO w FROM dq_ctl.veto_windows WHERE unit_key = p_unit ORDER BY eval_version DESC, window_id DESC LIMIT 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no veto window is open for this unit' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF p_reason_code IS NULL OR p_reason_code !~ '^[A-Z_]{3,32}$' THEN
    RAISE EXCEPTION 'a veto carries a reason code' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.vetoes(unit_key,window_id,state_version,person_id,authority,reason_code,jti,explanation_ref)
    VALUES (p_unit, w.window_id, p_expected_version, p_person, array_to_string(u.authorities, ','), p_reason_code,
            p_jti, p_explanation);
  nv := dq_ctl._transition(p_unit, p_expected_version, 'HELD', 'VETO', p_person,
          jsonb_build_object('veto', true, 'reason_code', p_reason_code, 'window_id', w.window_id));
  FOREACH auth IN ARRAY u.authorities LOOP          -- E8: the unit's state (the window's), not every state
    PERFORM dq_ctl._autonomy_demote(w.state, dq_ctl.seat_agent('dq_publish_svc'), 'RELEASE', auth, 'L2', 'VETO',
                                    'unit:' || p_unit, false);
  END LOOP;
  RETURN nv;
END $$;

-- ===== §1.3 track record: defined once in trace_schema.evidence_ddl (Part F R4-5: continuous, per code version) ===

-- ===== §3.1 one definition of a gating ABC failure (I1) ==========================================================
CREATE OR REPLACE VIEW dq_ctl.abc_gating_failures WITH (security_invoker = true) AS
  SELECT a.* FROM dq_ctl.abc a WHERE a.gating AND a.disposition <> 'PASS';

-- ===== Part F F-01: one definition of "ABC incomplete" ============================================================
-- A SEALED batch whose LANDING->GATED row is missing for any of its relations (a crash between the seal and the ABC
-- record). Its units are never evaluated (Evaluator guard) or released (publisher clause 12) until an operator
-- recovers the batch (Runtime.recover_batch_abc). batches.node holds the comma-joined relations (engine/seal.py), the
-- hop / measure names are engine/run.landing_to_gated_abc's. Read by services only: under the api's RLS on abc a
-- scoped caller would see rows as missing.
CREATE OR REPLACE VIEW dq_ctl.batches_abc_missing WITH (security_invoker = true) AS
  SELECT b.batch_id, r.rel FROM dq_ctl.batches b CROSS JOIN LATERAL unnest(string_to_array(b.node, ',')) AS r(rel)
  WHERE b.sealed AND NOT EXISTS (SELECT 1 FROM dq_ctl.abc a WHERE a.batch_id = b.batch_id
                                 AND a.hop = 'LANDING->GATED' AND a.measure = 'rows:' || split_part(r.rel, '.', 2));

-- ===== §3.2 feed items (I2) ======================================================================================
CREATE TABLE IF NOT EXISTS dq_ctl.feed_items(
  item_id bigserial PRIMARY KEY, state text NOT NULL, feed text NOT NULL CHECK (feed ~ '^[A-Z0-9_]{1,48}$'),
  kind text NOT NULL CHECK (kind IN (@FEED_KINDS@)), batch_id text, file_id text, authority text NOT NULL,
  period text, opened_at timestamptz NOT NULL DEFAULT clock_timestamp());
-- Part F: re-render the kind CHECK so an already-applied schema accepts the new kinds
ALTER TABLE dq_ctl.feed_items DROP CONSTRAINT IF EXISTS feed_items_kind_check;
ALTER TABLE dq_ctl.feed_items ADD CONSTRAINT feed_items_kind_check CHECK (kind IN (@FEED_KINDS@));
CREATE UNIQUE INDEX IF NOT EXISTS feed_items_once ON dq_ctl.feed_items(state, feed, kind, coalesce(batch_id, ''), coalesce(period, ''));
CREATE TABLE IF NOT EXISTS dq_ctl.feed_item_events(
  id bigserial PRIMARY KEY, item_id bigint NOT NULL REFERENCES dq_ctl.feed_items(item_id),
  event text NOT NULL CHECK (event IN ('OPEN','ACKNOWLEDGED')),
  reason_code text CHECK (reason_code IS NULL OR reason_code IN (@ACK@)), person_id text, jti text UNIQUE,
  explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref)), at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((event = 'ACKNOWLEDGED') = (reason_code IS NOT NULL AND person_id IS NOT NULL)));

-- an already-applied schema accepts the new codes
ALTER TABLE dq_ctl.feed_item_events DROP CONSTRAINT IF EXISTS feed_item_events_reason_code_check;
ALTER TABLE dq_ctl.feed_item_events ADD CONSTRAINT feed_item_events_reason_code_check
  CHECK (reason_code IS NULL OR reason_code IN (@ACK@));

CREATE OR REPLACE FUNCTION dq_ctl.feed_item_open(p_state text, p_feed text, p_kind text, p_batch text, p_file text,
  p_authority text, p_period text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE i bigint;
BEGIN
  IF session_user NOT IN ('dq_seal_svc','dq_registry_svc') THEN
    RAISE EXCEPTION 'feed items are opened by intake or the registry service' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO dq_ctl.feed_items(state,feed,kind,batch_id,file_id,authority,period)
    VALUES (p_state,p_feed,p_kind,p_batch,p_file,p_authority,p_period) ON CONFLICT DO NOTHING RETURNING item_id INTO i;
  IF i IS NULL THEN
    SELECT item_id INTO i FROM dq_ctl.feed_items WHERE state = p_state AND feed = p_feed AND kind = p_kind
       AND coalesce(batch_id,'') = coalesce(p_batch,'') AND coalesce(period,'') = coalesce(p_period,'');
    RETURN i;
  END IF;
  INSERT INTO dq_ctl.feed_item_events(item_id,event) VALUES (i,'OPEN');
  RETURN i;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.feed_item_ack(p_item bigint, p_person text, p_reason_code text, p_jti text DEFAULT NULL,
  p_explanation jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE i record;
BEGIN
  IF session_user <> 'dq_api' OR p_person IS NULL THEN
    RAISE EXCEPTION 'feed items are acknowledged by a named person through the api' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO i FROM dq_ctl.feed_items WHERE item_id = p_item;
  IF NOT FOUND OR NOT dq_ctl.in_scope(i.authority) THEN RAISE EXCEPTION 'unknown feed item' USING ERRCODE = 'no_data_found'; END IF;
  -- per kind: an unrouted file or lost recovery data is not acknowledged as "no activity" (and vice versa)
  IF p_reason_code IS NULL OR NOT (p_reason_code = ANY (CASE i.kind @ACK_BY_KIND@ ELSE ARRAY[]::text[] END)) THEN
    RAISE EXCEPTION 'reason_code for % one of %', i.kind, (CASE i.kind @ACK_BY_KIND@ ELSE ARRAY[]::text[] END)
      USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.feed_item_events WHERE item_id = p_item AND event = 'ACKNOWLEDGED') THEN
    RAISE EXCEPTION 'feed item already acknowledged' USING ERRCODE = 'unique_violation';
  END IF;
  IF p_explanation IS NULL OR NOT dq_ctl.expl_ref_ok(p_explanation) THEN     -- E3: the explanation shown is mandatory
    RAISE EXCEPTION 'an acknowledgement binds the explanation shown (explanation_ref)' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.feed_item_events(item_id,event,reason_code,person_id,jti,explanation_ref)
    VALUES (p_item,'ACKNOWLEDGED',p_reason_code,p_person,p_jti,p_explanation);
  PERFORM dq_ctl.append_audit('FEED_ITEM_ACKNOWLEDGED', encode(sha256(convert_to(p_item || '|' || p_reason_code || '|' || p_person, 'UTF8')), 'hex'));
END $$;

CREATE OR REPLACE VIEW dq_ctl.feed_items_status WITH (security_invoker = true) AS
  SELECT i.*, CASE WHEN a.id IS NULL THEN 'OPEN' ELSE 'ACKNOWLEDGED' END AS status, a.reason_code,
         a.person_id AS acknowledged_by, a.at AS acknowledged_at
  FROM dq_ctl.feed_items i
  LEFT JOIN LATERAL (SELECT e.* FROM dq_ctl.feed_item_events e WHERE e.item_id = i.item_id AND e.event = 'ACKNOWLEDGED'
                     ORDER BY e.id DESC LIMIT 1) a ON true;

-- ===== §3.3 parallel run (I3) ====================================================================================
CREATE TABLE IF NOT EXISTS dq_ctl.parallel_run_results(
  row_id bigserial PRIMARY KEY, run_id text NOT NULL, system text NOT NULL, period text NOT NULL, as_of text NOT NULL,
  relation text NOT NULL, measure text NOT NULL CHECK (measure ~ '^(records|amount:[A-Za-z0-9_.]{1,64}|rule_failed:[A-Za-z0-9_.@-]{1,96})$'),
  legacy numeric, new numeric, delta numeric, tolerance numeric,
  status text NOT NULL CHECK (status IN (@PRSTATUS@)), explain_code text CHECK (explain_code IS NULL OR explain_code IN (@EXPLAIN@)),
  authority text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (run_id, period, as_of, relation, measure));
CREATE TABLE IF NOT EXISTS dq_ctl.parallel_run_explanations(
  id bigserial PRIMARY KEY, row_id bigint NOT NULL REFERENCES dq_ctl.parallel_run_results(row_id),
  explain_code text NOT NULL CHECK (explain_code IN (@EXPLAIN@)), person_id text NOT NULL, jti text UNIQUE,
  step_up boolean NOT NULL, explanation_ref jsonb CHECK (dq_ctl.expl_ref_ok(explanation_ref)),
  at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE OR REPLACE FUNCTION dq_ctl.parallel_run_explain(p_row bigint, p_person text, p_code text, p_jti text,
  p_step_up boolean, p_explanation jsonb DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE r record; i bigint;
BEGIN
  IF session_user <> 'dq_api' OR p_person IS NULL THEN
    RAISE EXCEPTION 'explain codes are set by a named person through the api' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO r FROM dq_ctl.parallel_run_results WHERE row_id = p_row;
  IF NOT FOUND OR NOT dq_ctl.in_scope(r.authority) THEN RAISE EXCEPTION 'unknown parallel-run row' USING ERRCODE = 'no_data_found'; END IF;
  IF r.status <> 'BREAK' THEN RAISE EXCEPTION 'only a BREAK takes an explain code' USING ERRCODE = 'check_violation'; END IF;
  IF p_code IS NULL OR p_code NOT IN (@EXPLAIN@) THEN
    RAISE EXCEPTION 'explain_code one of %', ARRAY[@EXPLAIN@] USING ERRCODE = 'check_violation';
  END IF;
  IF p_code IN (@STEPUP@) AND (NOT coalesce(p_step_up, false) OR p_jti IS NULL) THEN
    RAISE EXCEPTION '% needs a step-up', p_code USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_explanation IS NULL OR NOT dq_ctl.expl_ref_ok(p_explanation) THEN     -- E3: the explanation shown is mandatory
    RAISE EXCEPTION 'an explain code binds the explanation shown (explanation_ref)' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.parallel_run_explanations(row_id,explain_code,person_id,jti,step_up,explanation_ref)
    VALUES (p_row,p_code,p_person,p_jti,coalesce(p_step_up,false),p_explanation) RETURNING id INTO i;
  PERFORM dq_ctl.append_audit('PARALLEL_RUN_EXPLAINED', encode(sha256(convert_to(p_row || '|' || p_code || '|' || p_person, 'UTF8')), 'hex'));
  RETURN i;
END $$;

CREATE OR REPLACE VIEW dq_ctl.parallel_run_status WITH (security_invoker = true) AS
  SELECT r.row_id, r.run_id, r.system, r.period, r.as_of, r.relation, r.measure, r.legacy, r.new, r.delta, r.tolerance,
         r.status, coalesce(x.explain_code, r.explain_code) AS explain_code, x.person_id AS explained_by,
         x.at AS explained_at, r.authority, r.at,
         (r.status <> 'BREAK' OR coalesce(x.explain_code, r.explain_code) IS NOT NULL) AS counts_for_go_no_go
  FROM dq_ctl.parallel_run_results r
  LEFT JOIN LATERAL (SELECT e.* FROM dq_ctl.parallel_run_explanations e WHERE e.row_id = r.row_id
                     ORDER BY e.id DESC LIMIT 1) x ON true;

-- ===== §6 monitoring metrics (aggregates only, computed in the data plane) =======================================
CREATE TABLE IF NOT EXISTS dq_ctl.monitor_metrics(
  id bigserial PRIMARY KEY, batch_id text NOT NULL, period text, relation text NOT NULL,
  metric text NOT NULL CHECK (metric ~ '^(rows|amount:[A-Za-z0-9_.]{1,64}|null_rate:[A-Z0-9_.]{1,64}|mix:[A-Z0-9_.]{1,64})$'),
  key text NOT NULL DEFAULT '' CHECK (key ~ '^[A-Za-z0-9_.:-]{0,64}$'), value numeric, n bigint CHECK (n IS NULL OR n >= 0),
  authority text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());

-- ===== §10b nightly-run simulator (DEC-018; demo mode only) =======================================================
-- One row per SIMULATED night, written once when the night's run finishes (append-only). The current simulated
-- business date is the latest night_date. person_id is NULL for nights preloaded at demo start.
CREATE TABLE IF NOT EXISTS dq_ctl.sim_runs(
  run_id text PRIMARY KEY CHECK (run_id ~ '^SIM-[0-9]{8}$'), night_date date NOT NULL UNIQUE,
  started_at timestamptz NOT NULL, finished_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  person_id text, files jsonb NOT NULL DEFAULT '[]'::jsonb, summary jsonb NOT NULL,
  CHECK (finished_at >= started_at));
-- E6: a night's lifecycle before its sim_runs row exists. STARTED is written BEFORE any file is ingested (with the
-- summary high-water marks, so a resumed night still counts everything it did); FAILED carries the error class.
-- A night with a STARTED and no sim_runs row is interrupted: run-next RESUMES it (sealed files are never ingested
-- again) or reports why it cannot. Append-only.
CREATE TABLE IF NOT EXISTS dq_ctl.sim_night_events(
  id bigserial PRIMARY KEY, run_id text NOT NULL CHECK (run_id ~ '^SIM-[0-9]{8}$'), night_date date NOT NULL,
  event text NOT NULL CHECK (event IN ('STARTED','RESUMED','FAILED')), person_id text,
  error_class text CHECK ((event = 'FAILED') = (error_class IS NOT NULL)), detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  at timestamptz NOT NULL DEFAULT clock_timestamp());

-- ===== append-only, RLS, grants ================================================================================
DO $$DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['eval_attestations','eval_attestation_confirmations','veto_windows','vetoes','feed_items','feed_item_events',
                           'parallel_run_results','parallel_run_explanations','monitor_metrics','sim_runs',
                           'sim_night_events'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_mut', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON dq_ctl.%I FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_mut', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_trunc', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON dq_ctl.%I FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_trunc', t);
    EXECUTE format('REVOKE ALL ON dq_ctl.%I FROM PUBLIC', t);
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON dq_ctl.%I FROM dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, '
                   'dq_verify_svc, dq_auditor, dq_triage_svc, dq_registry_svc, dq_monitor_svc', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END$$;

-- eval attestations: global (no authority); everyone who reads the track record may read them; written only by eval_attest
DROP POLICY IF EXISTS eval_att_read ON dq_ctl.eval_attestations;
CREATE POLICY eval_att_read ON dq_ctl.eval_attestations FOR SELECT TO dq_api, dq_registry_svc, dq_publish_svc, dq_eval_svc, dq_auditor USING (true);
DROP POLICY IF EXISTS eval_conf_read ON dq_ctl.eval_attestation_confirmations;
CREATE POLICY eval_conf_read ON dq_ctl.eval_attestation_confirmations FOR SELECT TO dq_api, dq_registry_svc, dq_publish_svc, dq_eval_svc, dq_auditor USING (true);
-- veto windows / vetoes: the api sees those of units in its scope; services see all; the evaluator opens windows
DROP POLICY IF EXISTS veto_windows_scope ON dq_ctl.veto_windows;
CREATE POLICY veto_windows_scope ON dq_ctl.veto_windows FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.unit_key = veto_windows.unit_key));
DROP POLICY IF EXISTS veto_windows_sys ON dq_ctl.veto_windows;
CREATE POLICY veto_windows_sys ON dq_ctl.veto_windows FOR SELECT TO dq_eval_svc, dq_publish_svc, dq_verify_svc, dq_registry_svc, dq_auditor USING (true);
DROP POLICY IF EXISTS veto_windows_ins ON dq_ctl.veto_windows;
CREATE POLICY veto_windows_ins ON dq_ctl.veto_windows FOR INSERT TO dq_eval_svc WITH CHECK (true);
DROP POLICY IF EXISTS vetoes_scope ON dq_ctl.vetoes;
CREATE POLICY vetoes_scope ON dq_ctl.vetoes FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.unit_key = vetoes.unit_key));
DROP POLICY IF EXISTS vetoes_sys ON dq_ctl.vetoes;
CREATE POLICY vetoes_sys ON dq_ctl.vetoes FOR SELECT TO dq_eval_svc, dq_publish_svc, dq_verify_svc, dq_registry_svc, dq_auditor USING (true);
-- feed items: scoped by authority for the api; opened by intake / registry through feed_item_open
DROP POLICY IF EXISTS feed_items_scope ON dq_ctl.feed_items;
CREATE POLICY feed_items_scope ON dq_ctl.feed_items FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS feed_items_sys ON dq_ctl.feed_items;
CREATE POLICY feed_items_sys ON dq_ctl.feed_items FOR SELECT TO dq_seal_svc, dq_registry_svc, dq_eval_svc, dq_publish_svc, dq_triage_svc, dq_auditor USING (true);
DROP POLICY IF EXISTS feed_item_events_scope ON dq_ctl.feed_item_events;
CREATE POLICY feed_item_events_scope ON dq_ctl.feed_item_events FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.feed_items i WHERE i.item_id = feed_item_events.item_id));
DROP POLICY IF EXISTS feed_item_events_sys ON dq_ctl.feed_item_events;
CREATE POLICY feed_item_events_sys ON dq_ctl.feed_item_events FOR SELECT TO dq_seal_svc, dq_registry_svc, dq_eval_svc, dq_publish_svc, dq_triage_svc, dq_auditor USING (true);
-- parallel run: scoped by authority; rows written by intake / evaluator; explain codes only through parallel_run_explain
DROP POLICY IF EXISTS pr_scope ON dq_ctl.parallel_run_results;
CREATE POLICY pr_scope ON dq_ctl.parallel_run_results FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS pr_sys ON dq_ctl.parallel_run_results;
CREATE POLICY pr_sys ON dq_ctl.parallel_run_results FOR ALL TO dq_seal_svc, dq_eval_svc USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS pr_read ON dq_ctl.parallel_run_results;
CREATE POLICY pr_read ON dq_ctl.parallel_run_results FOR SELECT TO dq_publish_svc, dq_registry_svc, dq_auditor USING (true);
DROP POLICY IF EXISTS prx_scope ON dq_ctl.parallel_run_explanations;
CREATE POLICY prx_scope ON dq_ctl.parallel_run_explanations FOR SELECT TO dq_api
  USING (EXISTS (SELECT 1 FROM dq_ctl.parallel_run_results r WHERE r.row_id = parallel_run_explanations.row_id));
DROP POLICY IF EXISTS prx_sys ON dq_ctl.parallel_run_explanations;
CREATE POLICY prx_sys ON dq_ctl.parallel_run_explanations FOR SELECT TO dq_seal_svc, dq_eval_svc, dq_publish_svc, dq_registry_svc, dq_auditor USING (true);
-- monitor metrics: written by the MONITOR seat only; the api reads its scope
DROP POLICY IF EXISTS mm_scope ON dq_ctl.monitor_metrics;
CREATE POLICY mm_scope ON dq_ctl.monitor_metrics FOR SELECT TO dq_api USING (dq_ctl.in_scope(authority));
DROP POLICY IF EXISTS mm_sys ON dq_ctl.monitor_metrics;
CREATE POLICY mm_sys ON dq_ctl.monitor_metrics FOR ALL TO dq_monitor_svc USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS mm_read ON dq_ctl.monitor_metrics;
CREATE POLICY mm_read ON dq_ctl.monitor_metrics FOR SELECT TO dq_triage_svc, dq_eval_svc, dq_publish_svc, dq_auditor USING (true);

-- sim runs: written by the intake / seal worker that runs the night; read by the api (operator screen) and the auditor
DROP POLICY IF EXISTS sim_runs_read ON dq_ctl.sim_runs;
CREATE POLICY sim_runs_read ON dq_ctl.sim_runs FOR SELECT TO dq_api, dq_seal_svc, dq_auditor USING (true);
DROP POLICY IF EXISTS sim_runs_ins ON dq_ctl.sim_runs;
CREATE POLICY sim_runs_ins ON dq_ctl.sim_runs FOR INSERT TO dq_seal_svc WITH CHECK (true);

GRANT USAGE ON SCHEMA dq_ctl TO dq_monitor_svc;
GRANT SELECT ON dq_ctl.sim_runs TO dq_api, dq_seal_svc, dq_auditor;
GRANT INSERT ON dq_ctl.sim_runs TO dq_seal_svc;
DROP POLICY IF EXISTS sim_night_events_read ON dq_ctl.sim_night_events;
CREATE POLICY sim_night_events_read ON dq_ctl.sim_night_events FOR SELECT TO dq_api, dq_seal_svc, dq_auditor USING (true);
DROP POLICY IF EXISTS sim_night_events_ins ON dq_ctl.sim_night_events;
CREATE POLICY sim_night_events_ins ON dq_ctl.sim_night_events FOR INSERT TO dq_seal_svc WITH CHECK (true);
GRANT SELECT ON dq_ctl.sim_night_events TO dq_api, dq_seal_svc, dq_auditor;
GRANT INSERT ON dq_ctl.sim_night_events TO dq_seal_svc;
GRANT USAGE ON SEQUENCE dq_ctl.sim_night_events_id_seq TO dq_seal_svc;
GRANT SELECT ON dq_ctl.eval_attestations, dq_ctl.eval_attestation_confirmations
  TO dq_api, dq_registry_svc, dq_publish_svc, dq_eval_svc, dq_auditor;
GRANT SELECT ON dq_ctl.veto_windows, dq_ctl.vetoes, dq_ctl.veto_window_status
  TO dq_api, dq_eval_svc, dq_publish_svc, dq_verify_svc, dq_registry_svc, dq_auditor;
GRANT INSERT ON dq_ctl.veto_windows TO dq_eval_svc;
GRANT SELECT ON dq_ctl.feed_items, dq_ctl.feed_item_events, dq_ctl.feed_items_status
  TO dq_api, dq_seal_svc, dq_registry_svc, dq_eval_svc, dq_publish_svc, dq_triage_svc, dq_auditor;
GRANT SELECT ON dq_ctl.parallel_run_results, dq_ctl.parallel_run_explanations, dq_ctl.parallel_run_status
  TO dq_api, dq_seal_svc, dq_eval_svc, dq_publish_svc, dq_registry_svc, dq_auditor;
GRANT INSERT ON dq_ctl.parallel_run_results TO dq_seal_svc, dq_eval_svc;
GRANT SELECT ON dq_ctl.monitor_metrics TO dq_api, dq_monitor_svc, dq_triage_svc, dq_eval_svc, dq_publish_svc, dq_auditor;
GRANT INSERT ON dq_ctl.monitor_metrics TO dq_monitor_svc;
GRANT USAGE ON SEQUENCE dq_ctl.veto_windows_window_id_seq, dq_ctl.parallel_run_results_row_id_seq,
  dq_ctl.monitor_metrics_id_seq TO dq_eval_svc, dq_seal_svc, dq_monitor_svc;
-- §3.1: every caller of the gating-failure definition (runtime, publisher, intake seat) reads the one view
GRANT SELECT ON dq_ctl.abc TO dq_seal_svc;
GRANT SELECT ON dq_ctl.abc_gating_failures
  TO dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_triage_svc, dq_monitor_svc;
GRANT SELECT ON dq_ctl.agents_current, dq_ctl.entitlements_current, dq_ctl.autonomy_current
  TO dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_triage_svc, dq_monitor_svc, dq_registry_svc, dq_auditor;

REVOKE ALL ON FUNCTION dq_ctl.tier_rank(text), dq_ctl.tier_ceiling(text), dq_ctl.class_rank(text), dq_ctl.ts_or_null(text),
  dq_ctl.autonomy_tier(text,text,text,text), dq_ctl.entitled(text,text,text), dq_ctl.agent_refusal(text,text,text,text),
  dq_ctl.gov_item_valid(text,jsonb,text), dq_ctl.gov_item_lower_only(text,jsonb), dq_ctl.gov_items_lower_only(text,jsonb),
  dq_ctl._no_literal_secrets(jsonb), dq_ctl.task_gate_ceiling(text,text), dq_ctl.autonomy_tier_for(text,text,text,text,text),
  dq_ctl.cadence_seconds(text), dq_ctl.feeds_lower_only(jsonb,jsonb), dq_ctl.minimums_lower_only(jsonb,jsonb),
  dq_ctl._autonomy_demote(text,text,text,text,text,text,text,boolean),
  dq_ctl.autonomy_demote(text,text,text,text,text,text,text),
  dq_ctl.eval_attest_confirm(bigint,text), dq_ctl._governance_person(text), dq_ctl.eval_confirm_two_person(),
  dq_ctl.veto_unit(text,int,text,text,text,jsonb), dq_ctl.feed_item_open(text,text,text,text,text,text,text),
  dq_ctl.feed_item_ack(bigint,text,text,text,jsonb), dq_ctl.parallel_run_explain(bigint,text,text,text,boolean,jsonb),
  dq_ctl.veto_window_clock() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.tier_rank(text), dq_ctl.tier_ceiling(text), dq_ctl.class_rank(text), dq_ctl.ts_or_null(text),
  dq_ctl.autonomy_tier(text,text,text,text), dq_ctl.entitled(text,text,text), dq_ctl.agent_refusal(text,text,text,text),
  dq_ctl.gov_items_lower_only(text,jsonb), dq_ctl.gov_item_lower_only(text,jsonb),
  dq_ctl.task_gate_ceiling(text,text), dq_ctl.autonomy_tier_for(text,text,text,text,text),
  dq_ctl.cadence_seconds(text), dq_ctl.feeds_lower_only(jsonb,jsonb), dq_ctl.minimums_lower_only(jsonb,jsonb)
  TO dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_triage_svc, dq_monitor_svc, dq_registry_svc, dq_auditor;
GRANT EXECUTE ON FUNCTION dq_ctl.autonomy_demote(text,text,text,text,text,text,text) TO dq_registry_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.eval_attest_confirm(bigint,text) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.veto_unit(text,int,text,text,text,jsonb) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.feed_item_open(text,text,text,text,text,text,text) TO dq_seal_svc, dq_registry_svc;
GRANT EXECUTE ON FUNCTION dq_ctl.feed_item_ack(bigint,text,text,text,jsonb) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.parallel_run_explain(bigint,text,text,text,boolean,jsonb) TO dq_api;
GRANT EXECUTE ON FUNCTION dq_ctl.expl_ref_ok(jsonb) TO dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_registry_svc;
-- Part F F-01: services read the one ABC-incomplete definition (the runtime, the evaluator, the publisher)
GRANT SELECT ON dq_ctl.batches_abc_missing TO dq_eval_svc, dq_seal_svc, dq_publish_svc;

-- ===== Part F R4-2: the deployment binding, DERIVED in the database, never asserted ================================
-- One row. dq_ctl.bind_deployment(p_deployment) derives the genesis kind itself: TEST when the TEST-GENESIS artifacts
-- exist, GOVERNED when a GENESIS change request was APPLIED, otherwise it refuses. TEST binds test | demo, GOVERNED
-- binds pilot | production. A second bind with the same values is a no-op; any other second bind is refused.
-- Readers treat a control plane with NO row as `production` (dqagent.deploy.bound).
CREATE TABLE IF NOT EXISTS dq_ctl.deployment_binding(one boolean PRIMARY KEY DEFAULT true CHECK (one),
  genesis_kind text NOT NULL CHECK (genesis_kind IN ('TEST','GOVERNED')),
  deployment text NOT NULL CHECK (deployment IN (@DEPLOYMENTS@)),
  idp_kind text NOT NULL CHECK (idp_kind IN ('LOCAL','OIDC')),
  at timestamptz NOT NULL DEFAULT clock_timestamp());
-- red team frame finding: the IdP kind is bound beside the deployment (a pilot on the local IdP is not production)
ALTER TABLE dq_ctl.deployment_binding ADD COLUMN IF NOT EXISTS idp_kind text NOT NULL DEFAULT 'LOCAL'
  CHECK (idp_kind IN ('LOCAL','OIDC'));
ALTER TABLE dq_ctl.deployment_binding ALTER COLUMN idp_kind DROP DEFAULT;
CREATE TABLE IF NOT EXISTS dq_ctl.deployment_events(id bigserial PRIMARY KEY,
  event text NOT NULL CHECK (event IN ('SIM_ENABLED_ON_GOVERNED')), person_id text,
  at timestamptz NOT NULL DEFAULT clock_timestamp());

DROP FUNCTION IF EXISTS dq_ctl.bind_deployment(text);
CREATE OR REPLACE FUNCTION dq_ctl.bind_deployment(p_deployment text, p_idp_kind text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE g text; cur record;
BEGIN
  IF session_user <> 'dq_registry_svc' THEN
    RAISE EXCEPTION 'only the registry service binds the deployment' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF EXISTS (SELECT 1 FROM dq_ctl.config_artifacts WHERE cr_id = 'TEST-GENESIS') THEN
    g := 'TEST';
  ELSIF EXISTS (SELECT 1 FROM dq_ctl.config_change_requests r JOIN dq_ctl.config_cr_events e USING (cr_id)
                WHERE r.origin = 'GENESIS' AND e.event = 'APPLIED') THEN
    g := 'GOVERNED';
  ELSE
    RAISE EXCEPTION 'no genesis to bind: the control plane holds neither TEST-GENESIS nor an applied GENESIS request'
      USING ERRCODE = 'check_violation';
  END IF;
  IF p_deployment IS NULL OR p_deployment NOT IN (@DEPLOYMENTS@) THEN
    RAISE EXCEPTION 'unknown deployment %', coalesce(p_deployment, 'NULL') USING ERRCODE = 'check_violation';
  END IF;
  IF g = 'TEST' AND p_deployment NOT IN ('test','demo') THEN
    RAISE EXCEPTION 'test genesis is refused under %', p_deployment USING ERRCODE = 'check_violation';
  END IF;
  IF g = 'GOVERNED' AND p_deployment NOT IN ('pilot','production') THEN
    RAISE EXCEPTION 'a governed control plane is not a % deployment', p_deployment USING ERRCODE = 'check_violation';
  END IF;
  IF p_idp_kind IS NULL OR p_idp_kind NOT IN ('LOCAL','OIDC') THEN
    RAISE EXCEPTION 'unknown IdP kind %', coalesce(p_idp_kind, 'NULL') USING ERRCODE = 'check_violation';
  END IF;
  IF p_deployment = 'production' AND p_idp_kind <> 'OIDC' THEN
    RAISE EXCEPTION 'production needs an OIDC IdP' USING ERRCODE = 'check_violation';
  END IF;
  SELECT * INTO cur FROM dq_ctl.deployment_binding;
  IF FOUND THEN
    IF cur.genesis_kind = g AND cur.deployment = p_deployment AND cur.idp_kind = p_idp_kind THEN RETURN g; END IF;
    RAISE EXCEPTION 'control plane is bound to % (% genesis, % IdP); refusing % with % IdP', cur.deployment,
      cur.genesis_kind, cur.idp_kind, p_deployment, p_idp_kind USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO dq_ctl.deployment_binding(genesis_kind, deployment, idp_kind) VALUES (g, p_deployment, p_idp_kind);
  PERFORM dq_ctl.append_audit('DEPLOYMENT_BOUND',
    encode(sha256(convert_to(g || '|' || p_deployment || '|' || p_idp_kind, 'UTF8')), 'hex'));
  RETURN g;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.deployment_event(p_event text, p_person text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE i bigint;
BEGIN
  IF session_user <> 'dq_registry_svc' THEN
    RAISE EXCEPTION 'only the registry service records deployment events' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO dq_ctl.deployment_events(event, person_id) VALUES (p_event, p_person) RETURNING id INTO i;
  PERFORM dq_ctl.append_audit(p_event, encode(sha256(convert_to(i::text || '|' || coalesce(p_person, ''), 'UTF8')), 'hex'));
  RETURN i;
END $$;

DO $$DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deployment_binding','deployment_events'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_mut', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON dq_ctl.%I FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_mut', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_trunc', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON dq_ctl.%I FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_trunc', t);
    EXECUTE format('REVOKE ALL ON dq_ctl.%I FROM PUBLIC', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END$$;
DROP POLICY IF EXISTS deployment_binding_read ON dq_ctl.deployment_binding;
CREATE POLICY deployment_binding_read ON dq_ctl.deployment_binding FOR SELECT
  TO dq_api, dq_eval_svc, dq_publish_svc, dq_registry_svc, dq_auditor, dq_owner USING (true);
DROP POLICY IF EXISTS deployment_binding_owner_ins ON dq_ctl.deployment_binding;
CREATE POLICY deployment_binding_owner_ins ON dq_ctl.deployment_binding FOR INSERT TO dq_owner WITH CHECK (true);
DROP POLICY IF EXISTS deployment_events_read ON dq_ctl.deployment_events;
CREATE POLICY deployment_events_read ON dq_ctl.deployment_events FOR SELECT
  TO dq_api, dq_registry_svc, dq_auditor, dq_owner USING (true);
DROP POLICY IF EXISTS deployment_events_owner_ins ON dq_ctl.deployment_events;
CREATE POLICY deployment_events_owner_ins ON dq_ctl.deployment_events FOR INSERT TO dq_owner WITH CHECK (true);
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON dq_ctl.deployment_binding, dq_ctl.deployment_events
  FROM dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, dq_auditor, dq_triage_svc, dq_registry_svc,
       dq_monitor_svc;
GRANT SELECT ON dq_ctl.deployment_binding TO dq_api, dq_eval_svc, dq_publish_svc, dq_registry_svc, dq_auditor;
GRANT SELECT ON dq_ctl.deployment_events TO dq_api, dq_registry_svc, dq_auditor;
REVOKE ALL ON FUNCTION dq_ctl.bind_deployment(text,text), dq_ctl.deployment_event(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.bind_deployment(text,text), dq_ctl.deployment_event(text,text) TO dq_registry_svc;

-- ===== Part F red team I5: single-use, server-side recovery challenges; ABC recorded once ==========================
-- A challenge is issued for (batch, person, sha256(reason)) and expires; consuming it inserts its id into
-- recovery_challenge_uses (PRIMARY KEY: exactly once, across processes). Both tables are append-only.
CREATE TABLE IF NOT EXISTS dq_ctl.recovery_challenges(challenge text PRIMARY KEY CHECK (challenge ~ '^[0-9a-f]{64}$'),
  batch_id text NOT NULL, person_id text NOT NULL, reason_sha256 text NOT NULL CHECK (reason_sha256 ~ '^[0-9a-f]{64}$'),
  issued_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS dq_ctl.recovery_challenge_uses(challenge text PRIMARY KEY
  REFERENCES dq_ctl.recovery_challenges(challenge), used_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE OR REPLACE FUNCTION dq_ctl.recovery_challenge_issue(p_batch text, p_person text, p_reason text, p_ttl_s int)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE ch text;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF p_batch IS NULL OR p_person IS NULL OR p_reason IS NULL OR length(p_reason) < 10
     OR p_ttl_s IS NULL OR p_ttl_s NOT BETWEEN 1 AND 3600 THEN
    RAISE EXCEPTION 'batch, person, a reason of at least 10 characters and a ttl of 1..3600 s'
      USING ERRCODE = 'check_violation';
  END IF;
  ch := encode(sha256(convert_to(gen_random_uuid()::text || gen_random_uuid()::text, 'UTF8')), 'hex');
  INSERT INTO dq_ctl.recovery_challenges(challenge, batch_id, person_id, reason_sha256, expires_at)
    VALUES (ch, p_batch, p_person, encode(sha256(convert_to(p_reason, 'UTF8')), 'hex'),
            clock_timestamp() + make_interval(secs => p_ttl_s));
  RETURN ch;
END $$;

CREATE OR REPLACE FUNCTION dq_ctl.recovery_challenge_consume(p_challenge text, p_batch text, p_person text, p_reason text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, dq_ctl, pg_temp AS $$
DECLARE r record;
BEGIN
  IF session_user <> 'dq_api' THEN RAISE EXCEPTION 'through the api' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF p_challenge IS NULL OR p_batch IS NULL OR p_person IS NULL OR p_reason IS NULL THEN   -- R5 L2: never NULL
    RAISE EXCEPTION 'recovery challenge, batch, person and reason are required' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO r FROM dq_ctl.recovery_challenges WHERE challenge = p_challenge;
  IF NOT FOUND OR r.batch_id IS DISTINCT FROM p_batch OR r.person_id IS DISTINCT FROM p_person
     OR r.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(p_reason, 'UTF8')), 'hex') THEN
    RAISE EXCEPTION 'recovery challenge not issued for this batch, person and reason' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF clock_timestamp() > r.expires_at THEN
    RAISE EXCEPTION 'recovery challenge expired' USING ERRCODE = 'insufficient_privilege';
  END IF;
  BEGIN
    INSERT INTO dq_ctl.recovery_challenge_uses(challenge) VALUES (p_challenge);
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'recovery challenge already used' USING ERRCODE = 'unique_violation';
  END;
  PERFORM dq_ctl.append_audit('RECOVERY_CHALLENGE_USED', encode(sha256(convert_to(p_challenge, 'UTF8')), 'hex'));
END $$;

DO $$DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['recovery_challenges','recovery_challenge_uses'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_mut', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON dq_ctl.%I FOR EACH ROW EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_mut', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON dq_ctl.%I', t || '_no_trunc', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON dq_ctl.%I FOR EACH STATEMENT EXECUTE FUNCTION dq_ctl.reject_mutation()', t || '_no_trunc', t);
    EXECUTE format('REVOKE ALL ON dq_ctl.%I FROM PUBLIC', t);
    EXECUTE format('REVOKE ALL ON dq_ctl.%I FROM dq_api, dq_eval_svc, dq_seal_svc, dq_publish_svc, dq_verify_svc, '
                   'dq_auditor, dq_triage_svc, dq_registry_svc, dq_monitor_svc', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE dq_ctl.%I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END$$;
DROP POLICY IF EXISTS recovery_challenges_owner ON dq_ctl.recovery_challenges;
CREATE POLICY recovery_challenges_owner ON dq_ctl.recovery_challenges FOR ALL TO dq_owner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS recovery_challenge_uses_owner ON dq_ctl.recovery_challenge_uses;
CREATE POLICY recovery_challenge_uses_owner ON dq_ctl.recovery_challenge_uses FOR ALL TO dq_owner USING (true) WITH CHECK (true);
REVOKE ALL ON FUNCTION dq_ctl.recovery_challenge_issue(text,text,text,int),
  dq_ctl.recovery_challenge_consume(text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dq_ctl.recovery_challenge_issue(text,text,text,int),
  dq_ctl.recovery_challenge_consume(text,text,text,text) TO dq_api;
-- the LANDING->GATED ABC of a batch is recorded once per relation, whichever process records it (the intake hops are
-- legitimately repeated within a batch: one row per file / program, so they are not part of this key)
CREATE UNIQUE INDEX IF NOT EXISTS abc_landing_gated_once ON dq_ctl.abc(batch_id, measure) WHERE hop = 'LANDING->GATED';
"""
