"""The default explainer: deterministic, offline, byte-stable plain-language text (Part E §7).

Every template says, for a non-technical State reviewer: what happened, what it means, and what the person can
do. A template prints ONLY fact values (numbers, identifiers and dispositions come from the facts), never a
causal claim (unless a HYPOTHESIS fact exists), and never advice to release or approve. The structured check in
check.py is run over every template in the tests, so a template that drifts from its facts fails the suite.

Kinds (KINDS) and the facts each reads (all optional unless noted; a missing fact drops its phrase):
  unit_card         unit_key, relation, authority, state*, tier, rules_checked, rules_not_passed,
                    approvals_required, approvals_recorded, veto_deadline, recommended_action
  rule_disposition  rule_id*, title, disposition*, failed, evaluated, relation, gating
  decision          decision_id, verb*, role, unit_key, cr_id, resulting_state, reason_code
  change_request    cr_id*, kinds, status, approvals_required, approvals_recorded, impact{units_reopened,
                    tier_from, tier_to, track_record_hash}
  lookback          lookback_id*, unit_key, rule_id, status*, in_live_manifest
  monitor_signal    relation*, metric, measure, element|column, period, status*, chart, rule, direction,
                    value/cl/ucl/lcl (xmr) | value_rate/cl_rate/ucl_rate/lcl_rate (p) | psi, bins (psi),
                    baseline_periods, min_periods, hypothesis
  parallel_break    relation*, measure*, period, as_of, legacy, new, delta, tolerance | tolerance_rate, status*,
                    explain_code, system
  feed_item         item_id, feed*, kind*, file, status, reason_code, cadence
  demotion          agent*, action_class, authority, from_tier, to_tier*, reason_code, ref
  autonomy          agent*, action_class, authority, tier*, ceiling_mode, track_record{...}
  cde               element*, status*, completeness_rate, validity_rate, completeness_min, validity_min,
                    steward_domain
  nightly_run       night_date*, run_id, files, abc_gating, abc_advisory, created_units, released_units,
                    veto_windows, held_units, held_reasons[{reason, units}], pending_units, feed_items,
                    monitor_signals, demotions (Part E §10b: a SIMULATED night of the demo; the text says so)
"""
from __future__ import annotations

KINDS = ("unit_card", "rule_disposition", "decision", "change_request", "lookback", "monitor_signal",
         "parallel_break", "feed_item", "demotion", "autonomy", "cde", "nightly_run")

TIER_NAMES = {"L4": "autonomous", "L3": "supervised, with a veto window", "L2": "recommend only",
              "L1": "assist only"}
TIER_MEANING = {
    "L4": "It may complete these actions on its own when every check is clean; rules that require a person still apply.",
    "L3": "It acts after a waiting period during which any authorised person can veto the action.",
    "L2": "It prepares a recommendation, and each action needs a person's approval.",
    "L1": "It gathers the evidence only; a person decides each action without a recommendation from the agent.",
}
STATE_PHRASE = {
    "HELD": "held for a person to review", "RELEASED": "released into the published data",
    "EVALUATING": "being checked", "PENDING_DEPENDENCY": "waiting until related eligibility data has been reviewed",
    "DENIED": "denied and kept out of the published data", "QUARANTINED": "set aside (quarantined)",
    "RECEIVED": "received and waiting to be checked",
}
ACTION_PHRASE = {"RELEASE": "releasing data", "POST_BOARD": "posting notes to the team board",
                 "PIN_MEMORY": "pinning reference baselines"}
EXPLAIN_CODES = {"TIMING": "a timing difference", "LEGACY_DEFECT": "a problem in the legacy system",
                 "NEW_DEFECT": "a problem in the new platform", "DEFINITION": "a difference in how the total is defined",
                 "FIXED": "a problem that was later fixed"}
FEED_REASONS = {"NO_ACTIVITY": "no activity", "HOLIDAY": "a holiday", "SUBMITTER_INACTIVE": "the sender is inactive",
                "RESUBMIT_REQUESTED": "a resubmission was requested",
                # N-T0-2: the codes of UNROUTED_FILE and RECOVERY_DATA_LOST items (governance_schema.FEED_ACK_CODES_BY_KIND)
                "ROUTE_PROPOSED": "a route was proposed through a change request",
                "FILE_REJECTED": "the file was rejected", "REDELIVERY_REQUESTED": "a redelivery was requested"}


def fmt(v) -> str:
    """Print a fact value exactly (thousands separators only); '<11' reads as 'fewer than 11'."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        s = f"{v:,.4f}".rstrip("0").rstrip(".")
        return s
    if v == "<11":
        return "fewer than 11"
    return str(v)


def money(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"${v:,.2f}"
    return fmt(v)


def _has(f, *ks):
    return all(f.get(k) is not None and f.get(k) != "" for k in ks)


def _join(parts) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def _authority(f) -> str:
    return f" under {f['authority']}" if _has(f, "authority") else ""


# ------------------------------------------------------------------------------------------------ kinds
def unit_card(f: dict) -> str:
    st = str(f.get("state", "")).upper()
    where = f" in {f['relation']}" if _has(f, "relation") else ""
    unit = f" (unit {f['unit_key']})" if _has(f, "unit_key") else ""
    what = f"These records{where}{unit}{_authority(f)} are {STATE_PHRASE.get(st, 'in state ' + st)}."
    checks = ""
    if _has(f, "rules_checked", "rules_not_passed"):
        if f["rules_not_passed"] == 0:
            checks = f"All {fmt(f['rules_checked'])} checks that apply came back clean."
        else:
            checks = (f"{fmt(f['rules_not_passed'])} of the {fmt(f['rules_checked'])} checks that apply "
                      "found something to look at.")
    tier = ""
    if _has(f, "tier"):
        t = f["tier"]
        tier = f"The agent works at autonomy level {t} ({TIER_NAMES.get(t, t)}) here."
        if t == "L1":
            tier += " Its own recommendation is not shown at this level."
        elif _has(f, "recommended_action"):
            tier += f" Its recommendation: {f['recommended_action']}."
    act = ""
    if st == "HELD":
        act = "You can review the evidence and approve or deny it."
        if _has(f, "approvals_required"):
            act += f" This needs {fmt(f['approvals_required'])} approving person(s)"
            act += (f"; {fmt(f['approvals_recorded'])} so far." if _has(f, "approvals_recorded") else ".")
    elif st == "EVALUATING" and _has(f, "veto_deadline"):
        act = (f"Unless someone vetoes it, it goes ahead automatically at {f['veto_deadline']}. "
               "You can veto it before then.")
    elif st == "PENDING_DEPENDENCY":
        act = "Nothing is needed from you yet; it will be checked again once the related data has been reviewed."
    elif st == "RELEASED":
        act = "No action is needed. If a problem is found later, it can be raised as a look-back item."
    return _join([what, checks, tier, act])


def rule_disposition(f: dict) -> str:
    title = f" ({f['title']})" if _has(f, "title") else ""
    where = f" on {f['relation']}" if _has(f, "relation") else ""
    d = str(f.get("disposition", "")).upper()
    what = f"Check {f.get('rule_id', '')}{title}{where} has the result {d}."
    detail = ""
    if _has(f, "failed", "evaluated"):
        detail = f"It looked at {fmt(f['evaluated'])} records and {fmt(f['failed'])} did not meet it."
    gating = str(f.get("gating", "")).lower()
    if gating == "advisory":
        means = "This check is advisory: it is reported, but on its own it does not stop the data going out."
    elif d in ("PASS",):
        means = "Nothing needs attention from this check."
    else:
        means = "This check counts towards whether the records need a person's review before they go out."
    act = ("" if d == "PASS" else
           "You can open the check, look at the summary of affected records, and agree next steps with the steward.")
    return _join([what, detail, means, act])


def decision(f: dict) -> str:
    who = f"A {f['role']}" if _has(f, "role") else "A person"
    on = (f" on unit {f['unit_key']}" if _has(f, "unit_key") else
          f" on change request {f['cr_id']}" if _has(f, "cr_id") else "")
    ref = f" (decision {f['decision_id']})" if _has(f, "decision_id") else ""
    what = f"{who} recorded a {str(f.get('verb', '')).upper()} decision{on}{ref}."
    reason = f"The reason given was {f['reason_code']}." if _has(f, "reason_code") else ""
    now = (f"The unit is now {STATE_PHRASE.get(str(f['resulting_state']).upper(), f['resulting_state'])}."
           if _has(f, "resulting_state") else "")
    means = "The decision is kept in the audit trail together with the explanation that was on screen."
    return _join([what, reason, now, means])


def change_request(f: dict) -> str:
    kinds = f.get("kinds")
    kinds = ", ".join(str(k) for k in kinds) if isinstance(kinds, list) else (kinds or "")
    what = f"Change request {f.get('cr_id', '')} proposes changes to {kinds or 'the governed configuration'}."
    status = f"Its status is {f['status']}." if _has(f, "status") else ""
    appr = ""
    if _has(f, "approvals_required"):
        appr = (f"It needs {fmt(f['approvals_required'])} approver(s) other than its author"
                + (f"; {fmt(f['approvals_recorded'])} recorded so far." if _has(f, "approvals_recorded") else "."))
    imp = f.get("impact") or {}
    impact = []
    if _has(imp, "tier_from", "tier_to"):
        impact.append(f"It moves an autonomy level from {imp['tier_from']} to {imp['tier_to']}.")
    if _has(imp, "units_reopened"):
        impact.append(f"If applied, {fmt(imp['units_reopened'])} unit(s) are checked again under the new settings.")
    if _has(imp, "track_record_hash"):
        impact.append(f"Approvers sign the track record they were shown (fingerprint {imp['track_record_hash']}).")
    act = "You can read the impact and the evidence, then approve or reject it."
    return _join([what, status, appr, *impact, act])


def lookback(f: dict) -> str:
    unit = f" for unit {f['unit_key']}" if _has(f, "unit_key") else ""
    rule = f", raised by check {f['rule_id']}" if _has(f, "rule_id") else ""
    what = f"Look-back item {f.get('lookback_id', '')}{unit}{rule} concerns data that was already published."
    status = f"Its status is {f['status']}." if _has(f, "status") else ""
    live = ""
    if f.get("in_live_manifest") is True:
        live = "Those records are still part of the published data."
    elif f.get("in_live_manifest") is False:
        live = "Those records are no longer part of the published data."
    act = ("You can confirm that the earlier decision stands, or withdraw the records so they are taken out "
           "of the published data.")
    return _join([what, status, live, act])


def _metric_label(f) -> str:
    m = f.get("measure")
    if m == "rows":
        return "number of records"
    if m == "amount":
        return f"total of {f['column']}" if _has(f, "column") else "dollar total"
    if m == "null_rate":
        return f"share of records with no {f['element']}" if _has(f, "element") else "share of empty values"
    if m == "mix":
        return f"mix of {f['element']} values" if _has(f, "element") else "mix of values"
    return str(f.get("metric", "measure"))


def monitor_signal(f: dict) -> str:
    rel = f.get("relation", "")
    per = f" in {f['period']}" if _has(f, "period") else ""
    label = _metric_label(f)
    st = str(f.get("status", "")).upper()
    means = ("This is an advisory signal: it compares this delivery with earlier ones and never holds any "
             "records on its own.")
    act = ("You can check with the sending team whether anything changed or is missing, and note what you find "
           "so the next review has the context.")
    if f.get("hypothesis"):
        means += f" A possible explanation on file: {f['hypothesis']}."
    if st == "BASELINE_BUILDING":
        return _join([f"Monitoring of the {label} for {rel} is still building its history:",
                      f"it has {fmt(f.get('baseline_periods'))} usable earlier periods and needs "
                      f"{fmt(f.get('min_periods'))}." if _has(f, "baseline_periods", "min_periods") else "",
                      "Until then no signals are raised for it."])
    if st == "PROVISIONAL":
        got = (f"About {fmt(f['received_pct'])}% of it has been received" if _has(f, "received_pct") else
               "Only part of it has been received")
        got += f" (deliveries through {f['received_through']})." if _has(f, "received_through") else "."
        return _join([f"{f.get('period') or 'This period'} is still open. {got}",
                      f"The {label} for {rel} is a provisional reading: it is compared with history only when the "
                      "period closes, and no signal is raised before then."])
    if st == "NOT_COMPUTED" and f.get("chart") != "psi":
        return _join([f"The {label} for {rel}{per} could not be compared with history:",
                      "there were no records in the period.", "No signal is raised."])
    chart = f.get("chart")
    if chart == "xmr" or (_has(f, "value", "ucl", "lcl") and chart != "psi"):
        is_money = f.get("measure") == "amount"
        show = money if is_money else fmt
        what = f"The {label} for {rel}{per} was {show(f.get('value'))}."
        hist = ""
        if _has(f, "lcl", "ucl"):
            n = f" over the previous {fmt(f['baseline_periods'])} periods" if _has(f, "baseline_periods") else ""
            hist = f"The usual range{n} is {show(f['lcl'])} to {show(f['ucl'])}"
            hist += f", around {show(f['cl'])}." if _has(f, "cl") else "."
        pos = ""
        if st == "SIGNAL":
            pos = ("This is below the usual range." if f.get("direction") == "LOW" else
                   "This is above the usual range." if f.get("direction") == "HIGH" else
                   "This is outside the usual pattern.")
        elif st == "OK":
            pos = "This is within the usual range."
        return _join([what, hist, pos, means if st == "SIGNAL" else "", act if st == "SIGNAL" else ""])
    if chart == "p":
        n = f" (out of {fmt(f['n'])} records)" if _has(f, "n") else ""
        what = f"The {label} for {rel}{per} was {fmt(f.get('value_rate_pct'))}%{n}."
        hist = ""
        if _has(f, "lcl_rate_pct", "ucl_rate_pct"):
            b = f" over the previous {fmt(f['baseline_periods'])} periods" if _has(f, "baseline_periods") else ""
            hist = (f"Based on the usual share{b}"
                    + (f" of {fmt(f['cl_rate_pct'])}%" if _has(f, "cl_rate_pct") else "")
                    + f", a delivery of this size is expected between {fmt(f['lcl_rate_pct'])}% and "
                      f"{fmt(f['ucl_rate_pct'])}%.")
        pos = ""
        if st == "SIGNAL":
            pos = ("This is higher than expected." if f.get("direction") == "HIGH" else
                   "This is lower than expected.")
        elif st == "OK":
            pos = "This is within the expected range."
        return _join([what, hist, pos, means if st == "SIGNAL" else "", act if st == "SIGNAL" else ""])
    # psi (mix)
    if st == "NOT_COMPUTED":
        return _join([f"The {label} for {rel}{per} could not be compared with history:",
                      "there are not enough records in some groups for a fair comparison.",
                      "No signal is raised."])
    what = f"The {label} for {rel}{per} has a stability index of {fmt(f.get('psi'))}."
    scale = ""
    if _has(f, "psi_watch", "psi_shift"):
        scale = (f"An index of {fmt(f['psi_watch'])} or more is worth watching, and {fmt(f['psi_shift'])} "
                 "or more is a clear shift.")
    bins = "; ".join(f"{b['category']}: {fmt(b['now_pct'])}% now, usually {fmt(b['usual_pct'])}%"
                     for b in (f.get("bins") or []) if _has(b, "category", "now_pct", "usual_pct"))
    bins = f"By group: {bins}." if bins else ""
    pos = {"SHIFT": "The mix has clearly shifted.", "WATCH": "The mix has moved enough to watch.",
           "OK": "The mix is in line with history."}.get(st, "")
    sig = st in ("SHIFT", "WATCH")
    return _join([what, scale, bins, pos, means if sig else "", act if sig else ""])


def parallel_break(f: dict) -> str:
    m = str(f.get("measure", ""))
    kind = m.split(":")[0]
    show = money if kind == "amount" else fmt
    label = ("record count" if kind == "records" else
             f"total of {m.split(':', 1)[1]}" if kind == "amount" and ":" in m else
             f"failure count for check {m.split(':', 1)[1]}" if kind == "rule_failed" and ":" in m else m)
    per = f" for {f['period']}" if _has(f, "period") else ""
    asof = f" as of {f['as_of']}" if _has(f, "as_of") else ""
    sysname = f"The legacy system ({f['system']})" if _has(f, "system") else "The legacy system"
    what = (f"{sysname} reports a {label} of {show(f.get('legacy'))} for {f.get('relation', '')}{per}{asof}; "
            f"the new platform has {show(f.get('new'))}.")
    st = str(f.get("status", "")).upper()
    tol = (f"{fmt(f['tolerance_rate_pct'])}%" if _has(f, "tolerance_rate_pct") else
           fmt(f["tolerance"]) if _has(f, "tolerance") else "")
    if st == "MATCH":
        res = "Both totals agree exactly."
    else:
        d = f.get("delta")
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            diff = f"The new platform's figure is {show(abs(d))} {'lower' if d < 0 else 'higher'}"
        else:
            diff = "The new platform has no matching figure"
        side = "inside" if st == "WITHIN" else "outside"
        res = f"{diff}, which is {side} the allowed tolerance" + (f" of {tol}." if tol else ".")
    means = "This comparison is evidence for the switch-over decision; it never holds any records."
    act = ""
    if st == "BREAK":
        if _has(f, "explain_code"):
            act = f"It has been explained as {EXPLAIN_CODES.get(f['explain_code'], f['explain_code'])}."
        else:
            act = ("A named person needs to record what the difference is: timing, a problem in the legacy "
                   "system, a problem in the new platform, a difference in definition, or already fixed. "
                   "Until then it counts as unexplained.")
    return _join([what, res, means, act])


def feed_item(f: dict) -> str:
    k = str(f.get("kind", "")).upper()
    feed = f.get("feed", "")
    status = f"Item {f['item_id']} is {f['status']}." if _has(f, "item_id", "status") else ""
    reason = (f"It was acknowledged with the reason: {FEED_REASONS.get(f['reason_code'], f['reason_code'])}."
              if _has(f, "reason_code") else "")
    if k == "UNROUTED_FILE":                          # N-T0-2: a file that matched no route; it was never loaded
        batch = f" (batch {f['batch_id']})" if _has(f, "batch_id") else ""
        what = (f"An inbound file{batch} matched no route of the feed configuration, so it was not loaded and no "
                "check ran on it.")
        means = ("Nothing from this file reached the data or any decision. It stays out until a route that matches "
                 "it is added through a change request.")
        act = ("" if reason else "The intake team can acknowledge it with a reason: a route was proposed through a "
               "change request, the file was rejected, or a resubmission was requested.")
        return _join([what, status, reason, means, act])
    if k == "RECOVERY_DATA_LOST":                     # N-T0-2: recovery refused; the frozen loads are gone (R4-1)
        batch = f"batch {f['batch_id']}" if _has(f, "batch_id") else "a batch"
        what = (f"Recovery of {batch} was refused: data the batch was sealed with is no longer in the data plane, so "
                "its control totals cannot be recomputed from the same rows.")
        means = "The batch stays held. Nothing from it was released."
        act = ("" if reason else "The intake team can acknowledge it with a reason: a redelivery was requested, or a "
               "resubmission was requested.")
        return _join([what, status, reason, means, act])
    if k == "EMPTY_DELIVERY":
        file = f" ({f['file']})" if _has(f, "file") else ""
        what = f"A delivery from feed {feed}{file} arrived with no records, although this feed normally has activity."
    else:
        cad = f" ({f['cadence']})" if _has(f, "cadence") else ""
        what = f"No delivery from feed {feed} arrived within its expected schedule{cad}."
    means = "This does not hold any records; it is a prompt to confirm the situation with the sender."
    act = ("" if reason else "You can acknowledge it with a reason: no activity, a holiday, the sender is inactive, "
           "or a resubmission was requested.")
    return _join([what, status, reason, means, act])


def demotion(f: dict) -> str:
    action = ACTION_PHRASE.get(f.get("action_class"), str(f.get("action_class", "")).lower())
    frm = f" from {f['from_tier']}" if _has(f, "from_tier") else ""
    what = (f"The agent {f.get('agent', '')} was moved down{frm} to autonomy level {f.get('to_tier', '')} "
            f"for {action}{_authority(f)}.")
    rc = str(f.get("reason_code", "")).upper()
    trig = {"VETO": "a person vetoed an action it had started", "LOOKBACK_WITHDRAWN":
            "records it had published were later withdrawn"}.get(rc, rc.lower().replace("_", " "))
    when = (f"This happened automatically after {trig}" + (f" ({f['ref']})." if _has(f, "ref") else ".")
            if trig else "")
    means = TIER_MEANING.get(f.get("to_tier"), "")
    act = "Moving it back up is only possible through a change request approved by State approvers."
    return _join([what, when, means, act])


def autonomy(f: dict) -> str:
    t = f.get("tier", "")
    action = ACTION_PHRASE.get(f.get("action_class"), str(f.get("action_class", "")).lower())
    what = (f"The agent {f.get('agent', '')} works at autonomy level {t} ({TIER_NAMES.get(t, t)}) for "
            f"{action}{_authority(f)}.")
    means = TIER_MEANING.get(t, "")
    tr = f.get("track_record") or {}
    parts = [(k, lbl) for k, lbl in (("autonomous", "autonomous release(s)"),
                                      ("after_veto_window", "release(s) after a veto window"),
                                      ("vetoes", "veto(es)"), ("overrides", "human override(s)"),
                                      ("denies", "denial(s) after release"),
                                      ("lookback_withdrawn", "look-back withdrawal(s)")) if _has(tr, k)]
    rec = ("From the time this level was set: " + ", ".join(f"{fmt(tr[k])} {lbl}" for k, lbl in parts) + "."
           if parts else "")
    act = ("A level never rises on its own. Raising it needs a change request approved by State approvers; "
           "it drops automatically after a veto or a withdrawal.")
    return _join([what, means, rec, act])


def cde(f: dict) -> str:
    el = f.get("element", "a critical data element")
    st = str(f.get("status", "")).upper()
    dom = f" (stewarded by {f['steward_domain']})" if _has(f, "steward_domain") else ""
    nums = []
    if _has(f, "completeness_rate_pct"):
        nums.append(f"{fmt(f['completeness_rate_pct'])}% complete"
                    + (f" (minimum {fmt(f['completeness_min_pct'])}%)" if _has(f, "completeness_min_pct") else ""))
    if _has(f, "validity_rate_pct"):
        nums.append(f"{fmt(f['validity_rate_pct'])}% valid"
                    + (f" (minimum {fmt(f['validity_min_pct'])}%)" if _has(f, "validity_min_pct") else ""))
    nums_s = " and ".join(nums)
    if st == "MEETS":
        what = f"Critical data element {el}{dom} meets its minimum quality" + (f": {nums_s}." if nums_s else ".")
    elif st == "BELOW":
        what = (f"Critical data element {el}{dom} is below its minimum quality"
                + (f": {nums_s}." if nums_s else "."))
    elif st == "NOT_LOADED":
        what = (f"The validity of critical data element {el}{dom} cannot be confirmed yet: the code list it is "
                "checked against is not loaded.")
    else:
        what = (f"Critical data element {el}{dom} is not measured yet: the data that carries it is not delivered "
                "or not checked.")
    means = "This is advisory: it shows where data quality stands, and it does not hold any records."
    act = ("You can raise it with the data steward for this area." if st in ("BELOW", "NOT_LOADED") else "")
    return _join([what, means, act])


def nightly_run(f: dict) -> str:
    run = f" (run {f['run_id']})" if _has(f, "run_id") else ""
    what = f"Simulated night {f.get('night_date', '')}{run}: {fmt(f.get('files', 0))} file(s) arrived."
    abc = ""
    if _has(f, "abc_gating", "abc_advisory"):
        abc = (f"The balance checks recorded {fmt(f['abc_gating'])} gating and {fmt(f['abc_advisory'])} advisory "
               "break(s).")
    units = ""
    if _has(f, "created_units"):
        units = f"{fmt(f['created_units'])} unit(s) of records were created."
    agent = []
    if _has(f, "released_units"):
        agent.append(f"{fmt(f['released_units'])} unit(s) were released by the agent on its own")
    if _has(f, "veto_windows"):
        agent.append(f"{fmt(f['veto_windows'])} veto window(s) were opened")
    if _has(f, "held_units"):
        agent.append(f"{fmt(f['held_units'])} unit(s) were held for a person to review")
    agent_s = ("During the night, " + "; ".join(agent) + ".") if agent else ""
    why = "; ".join(f"{r['reason']}: {fmt(r['units'])}" for r in (f.get("held_reasons") or [])
                    if _has(r, "reason", "units"))
    why = f"Held by reason: {why}." if why else ""
    wait = (f"{fmt(f['pending_units'])} unit(s) are waiting until related eligibility data has been reviewed."
            if _has(f, "pending_units") else "")
    other = []
    if _has(f, "feed_items"):
        other.append(f"{fmt(f['feed_items'])} feed item(s) were opened")
    if _has(f, "monitor_signals"):
        other.append(f"{fmt(f['monitor_signals'])} monitoring signal(s) were raised")
    if _has(f, "demotions"):
        other.append(f"{fmt(f['demotions'])} autonomy demotion(s) were recorded")
    other_s = ("Also: " + "; ".join(other) + ".") if other else ""
    means = "This is a simulation over synthetic demo data; no real delivery was received."
    act = "You can open the held units and signals from this run to review them, or run the next simulated night."
    return _join([what, abc, units, agent_s, why, wait, other_s, means, act])


RENDER = {k: globals()[k] for k in KINDS}


def render(kind: str, facts: dict) -> str:
    if facts.get("part2_withheld"):
        return ("Details are withheld because these records come from a program with extra confidentiality "
                "protection. An authorised reviewer can see the evidence on the record itself.")
    if kind not in RENDER:
        raise ValueError(f"unknown explanation kind {kind!r}; known: {', '.join(KINDS)}")
    return RENDER[kind](facts)


# ------------------------------------------------------------------------------------------------ citations (P1-1 §D)
import hashlib as _hashlib                     # noqa: E402
from pathlib import Path as _Path               # noqa: E402

# a content digest of this module: a template change gives a new version, so cached explanations re-key (I1)
TEMPLATE_VERSION = "t-" + _hashlib.sha256(_Path(__file__).read_bytes()).hexdigest()[:12]
CITATION_LABEL = "generated_from_facts"


class _Tracked(dict):
    """A read-recording view of the facts: every key a template reads (and finds) is recorded, dotted as
    llm.facts.flatten spells it. Rendering through it is byte-identical to rendering the plain dict."""

    def __init__(self, data, seen: set, prefix: str = ""):
        super().__init__(data)
        self._seen, self._prefix = seen, prefix

    def _wrap(self, k, v):
        key = f"{self._prefix}{k}"
        if isinstance(v, dict):
            return _Tracked(v, self._seen, key + ".")
        if isinstance(v, list):
            return [(_Tracked(x, self._seen, f"{key}.{i}.") if isinstance(x, dict) else x) for i, x in enumerate(v)]
        return v

    def _mark(self, k, v):
        key = f"{self._prefix}{k}"
        if isinstance(v, list):
            for i, x in enumerate(v):
                if not isinstance(x, dict):
                    self._seen.add(f"{key}.{i}")
        elif not isinstance(v, dict):
            self._seen.add(key)

    def __getitem__(self, k):
        v = super().__getitem__(k)
        self._mark(k, v)
        return self._wrap(k, v)

    def get(self, k, default=None):
        if k in self.keys():
            return self[k]
        return default

    def items(self):
        return [(k, self[k]) for k in self.keys()]

    def values(self):
        return [self[k] for k in self.keys()]


def render_with_claims(kind: str, facts: dict) -> tuple[str, list[dict]]:
    """render() plus the facts it read, as claims [{fact_key, value}] (byte-identical text). The claims are
    citations labelled CITATION_LABEL: the template is generated from the facts; it is not fact-checked."""
    from dqagent.llm.facts import flatten
    seen: set[str] = set()
    text = render(kind, _Tracked(facts, seen))
    flat = flatten(facts)
    return text, [{"fact_key": k, "value": flat[k]} for k in sorted(seen)
                  if k in flat and k != "explanation_kind" and k != "part2_withheld"]
