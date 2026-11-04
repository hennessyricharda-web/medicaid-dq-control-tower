"""dqagent CLI (T1).

  dqagent demo   --pg-port 55432 --data <dir>    : load a synthetic run through P4 → P6 and serve
                                                   API :8080, local IdP :8081 (T1 only), worker loop
                 [--genesis test|governed]       : test = labelled TEST-GENESIS (default); governed = the seed is
                                                   proposed as a GENESIS change request and ratified by TWO other
                                                   people (Part E C4)
  Part E extras (when present in --data): monitor_history.json is loaded as the MONITOR seat's history BEFORE the
  load, the monitoring period is closed after it, and legacy_totals.json is compared (parallel run) after it.
                 [--nightly [--preload-nights K] [--earn-story] [--allow-sim]]: Part E §10b nightly-run simulator
                                                   (demo mode only): nights 1..K (default 15) are run at startup,
                                                   the rest stay QUEUED for POST /api/v1/sim/run-next (operations
                                                   duty). --earn-story starts RELEASE AUTH_MEDICAID at L2 (lower-only
                                                   demotion) so the earned-autonomy arc can be shown live. The
                                                   simulator is refused with --genesis governed unless --allow-sim
  Deployments (Part F F-15): --genesis test runs as the `demo` deployment (labelled test keys, local IdP);
  --genesis governed runs as `pilot`: keys from DQ_K_EV / DQ_K_FP / DQ_K_ABC (it exits if any is missing), the local
  IdP with the "not production" chip. --allow-sim (governed + --nightly only) permits exactly the /sim endpoints,
  preloading synthetic nights and --earn-story, and records SIM_ENABLED_ON_GOVERNED; never test keys, test genesis,
  DQ_ALLOW_TEST_GENESIS, the scripted transport or the local IdP under production.
  Recovery of a batch sealed without its ABC (Part F F-01) is API-only (POST /api/v1/recovery/batches/{id}/abc):
  it needs the operations duty and a single-use step-up, which only the api holds.
  dqagent harness validate|plan                  : validate the harness and print the global order
  dqagent eval run --suite agent_eval|explainer  : Part F K0 / PFP P2-1: runs the named suite for real and writes
                                                   ONE dq_ctl.eval_runs row as the eval service (dqagent.eval.recorder).
                                                   source_mode is always derived from what actually ran, never a flag.
"""
from __future__ import annotations

import argparse
import threading
import time
import uuid
from pathlib import Path

import yaml


def _pg(args):
    return dict(host=args.pg_host, port=args.pg_port, user=args.pg_user)


def cmd_harness(args):
    from dqagent.harness.plan import order, validate
    h = yaml.safe_load(open(args.config))
    errs = validate(h)
    print("VALID" if not errs else "INVALID")
    for e in errs:
        print("  -", e)
    print("order:", " -> ".join(order(h)))
    return 0 if not errs else 2


def cmd_eval(args):
    if args.eval_cmd != "run":
        raise SystemExit("dqagent eval: only 'run' is implemented")
    from dqagent.engine.cp import CP
    from dqagent.eval.recorder import run_suite
    cp = CP(dict(dbname=args.db, **_pg(args)))
    row = run_suite(cp, args.suite)
    print("eval run:", {k: row[k] for k in ("run_id", "suite", "source_mode", "cases", "passed", "code_sha256")})
    return 0


def cmd_backtest(args):
    from dqagent.backtest import run_backtest
    report = run_backtest(pipeline_id=args.pipeline, cr_id=args.cr, nights=args.nights, data=args.data,
                          state=args.state, pg=_pg(args), live_db=args.db)
    print("backtest:", report["report_id"], "digest", report["digest"][:16], "label", report["label"])
    if report.get("delta_note"):
        print("delta_note:", report["delta_note"])
    print("deltas:", report["deltas"])
    return 0


from dqagent.runtime import PARTNERS, arrival_order, synthetic_nights  # noqa: E402,F401  (re-exported)


def load_synthetic(rt, data: Path, *, drain_passes: int = 3, parte: bool = False) -> dict:
    """Load a synthetic run the way it would arrive: night by night (one night per delivery date), ONE batch per
    inbound file in arrival order, with the worker cycle (verify → publish → re-evaluate) between arrivals. IE and
    834 feed P4; 837P (+ translator acks) feed P6. Every night goes through Runtime.run_night, the same night-runner
    the nightly simulator uses (Part E §10b). Returns {file name: run summary}.
    parte: also run the Part E extras present in `data` (monitor history before; on the last night the monitoring
    period is closed and the parallel run compared, under "_parte")."""
    data = Path(data)
    out: dict = {}
    if parte:
        load_parte_history(rt, data)
    nights = synthetic_nights(data, rt.routing)          # F-02: the EFFECTIVE (governed) routing
    last = None
    for i, (night, files) in enumerate(nights):
        last = rt.run_night(data, night, files, last=(i == len(nights) - 1), parte=parte, drain_passes=drain_passes)
        out.update(last["file_results"])
    if parte:
        out["_parte"] = (last or {}).get("parte") or {"monitor": [], "parallel_run": []}
    return out


def load_parte_history(rt, data: Path) -> dict | None:
    """Part E §6: monitor_history.json (if present) becomes the MONITOR seat's history and pinned baselines."""
    return rt.load_run_history(Path(data))


def cmd_demo(args):
    import psycopg
    import uvicorn

    from dqagent.api.app import create_app, create_idp_app
    from dqagent.cp import schema
    from dqagent.runtime import Runtime
    nightly = getattr(args, "nightly", False)
    if nightly and getattr(args, "genesis", "test") == "governed" and not getattr(args, "allow_sim", False):
        # nit: a governed control plane is not a demo; simulated nights there need an explicit, visible opt-in
        raise SystemExit("--nightly (SIMULATED nights) is refused with --genesis governed; pass --allow-sim to run "
                         "the simulator on a governed control plane deliberately")
    if getattr(args, "earn_story", False) and not nightly:
        raise SystemExit("--earn-story is a nightly-demo option: use it with --nightly")
    if getattr(args, "allow_sim", False) and not (nightly and getattr(args, "genesis", "test") == "governed"):
        raise SystemExit("--allow-sim applies only to --genesis governed --nightly")
    governed = getattr(args, "genesis", "test") == "governed"
    keys = None
    if governed:
        # I4: a governed control plane is never paired with test keys; the pilot deployment reads them from the env
        from dqagent.deploy import NotConfiguredError
        from dqagent.secrets import EnvSecrets
        try:
            keys = EnvSecrets().keys()
        except NotConfiguredError as e:
            raise SystemExit(f"--genesis governed: {e} (set DQ_K_EV, DQ_K_FP, DQ_K_ABC)") from e
    pg = _pg(args)
    db = args.db or "dqcp_demo"
    with psycopg.connect(dbname="postgres", autocommit=True, **pg) as a:
        a.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        a.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **pg)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    # A fresh CP must never be paired with a DP file left over from an earlier run: the gated relations would
    # carry rows the CP has no units for (ABC catches it as LANDING->GATED residual; refuse up front instead).
    dp_file = Path(args.duckdb)
    for f in (dp_file, Path(str(dp_file) + ".wal")):
        if f.exists():
            f.unlink()
    from dqagent.tenant import load_tenant
    tenant = load_tenant(getattr(args, "tenant", None))
    if getattr(args, "genesis", "test") == "governed":
        # the seed is a GENESIS change request ratified by two people other than the author (database-enforced)
        from dqagent.runtime import bootstrap_roster
        bootstrap_roster(dsn, tenant, "ROSTER-BOOTSTRAP")
        rt = Runtime(dsn, dp_path=args.duckdb, tenant=tenant, deployment="pilot", keys=keys)
        g = rt.ratify_genesis(author="maker", approvers=tuple(args.genesis_approvers.split(",")))
        print("genesis:", g)
        if g["status"] != "APPLIED":
            raise SystemExit(f"genesis not ratified: {g}")
    else:
        # K6 Front 1: bind the demo deployment to transport: scripted (deploy.py already allow-lists it for
        # `demo`) via a per-runtime override, never by editing the single global config/explainer.yaml — that
        # file is also read as-is by `--genesis governed` (pilot) and by tests exercising pilot deployment
        # directly; changing it there would flip their explainer behavior too. ScriptedTransport calls no
        # model (dqagent/llm/transports.py), so reusing the already-defined `providers.anthropic` block (no
        # secrets, env-only per config/explainer.yaml's own convention) is safe with no real credentials set.
        from dqagent import llm
        demo_explainer_cfg = {**llm.load_config(), "provider": "anthropic", "transport": "scripted"}
        rt = Runtime(dsn, dp_path=args.duckdb, test_genesis=True,   # T1 TEST genesis, labelled in the Tower
                     tenant=tenant, deployment="demo", explainer_cfg=demo_explainer_cfg)
    if getattr(args, "nightly", False):
        # Part E §10b: SIMULATED nights. Nights 1..K now; the rest stay QUEUED for an operator's "Run next night".
        rt.sim_enable(Path(args.data), parte=True, allow_governed=getattr(args, "allow_sim", False),
                      person_id=rt.idp.users["maker"]["emp_id"] if governed else None)
        if getattr(args, "earn_story", False):
            print("earn story: RELEASE AUTH_MEDICAID starts at L2 (lower-only demotion):", rt.earn_story_seed())
        for r in rt.sim_preload(args.preload_nights):
            print("night", r["night_date"], {k: v["n"] for k, v in r["counts"].items()})
        print("simulator:", rt.sim_status())
    else:
        summary = load_synthetic(rt, Path(args.data), parte=True)
        parte = summary.pop("_parte", {})
        print("loaded:", {k: v["summary"] for k, v in summary.items()})
        print("monitor:", sorted((r["relation"], r["metric"], r["key"], r["status"]) for r in parte.get("monitor", [])
                                 if r["status"] not in ("OK",)))
        print("parallel run:", sorted((r["relation"], r["measure"], r["status"]) for r in parte.get("parallel_run", [])))

    import logging
    log = logging.getLogger("dqagent.worker")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    def worker():
        failures = 0
        while True:
            time.sleep(3)
            try:
                rt.verify_and_publish()
                failures = 0
            except Exception:  # noqa: BLE001
                failures += 1          # logged with traceback; publication fails closed (pointer unchanged)
                log.exception("verify/publish cycle failed (consecutive failures: %d)", failures)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=lambda: uvicorn.run(create_idp_app(rt.idp), host="127.0.0.1", port=8081,
                                                log_level="warning"), daemon=True).start()
    uvicorn.run(create_app(rt), host="127.0.0.1", port=8080, log_level="warning")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dqagent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harness")
    h.add_argument("action", choices=["validate", "plan"])
    h.add_argument("--config", default="config/harness_w1.yaml")
    d = sub.add_parser("demo")
    d.add_argument("--data", required=True)
    d.add_argument("--pg-host", default="/tmp")
    d.add_argument("--pg-port", type=int, default=55432)
    d.add_argument("--pg-user", default="dq_gov")
    d.add_argument("--db", default=None)
    d.add_argument("--duckdb", default="dq_t1.duckdb")
    d.add_argument("--tenant", default=None, help="tenant profile YAML kept OUTSIDE the repo (display + approver org)")
    d.add_argument("--genesis", choices=["test", "governed"], default="test",
                   help="test: TEST-GENESIS (T1 label); governed: GENESIS change request ratified by two approvers")
    d.add_argument("--genesis-approvers", default="checker,checker2",
                   help="local-IdP users who ratify a governed genesis (two, other than the author 'maker')")
    d.add_argument("--nightly", action="store_true",
                   help="Part E §10b nightly-run simulator (demo mode only): load night by night, SIMULATED")
    d.add_argument("--preload-nights", type=int, default=15,
                   help="with --nightly: nights run at startup (default 15); the rest stay queued")
    d.add_argument("--earn-story", action="store_true",
                   help="with --nightly: start RELEASE AUTH_MEDICAID at L2 so a two-person promotion to L3 opens "
                        "real veto windows on the next simulated night")
    d.add_argument("--allow-sim", action="store_true",
                   help="allow --nightly with --genesis governed (refused otherwise)")
    e = sub.add_parser("eval")
    e2 = e.add_subparsers(dest="eval_cmd", required=True)
    er = e2.add_parser("run")
    er.add_argument("--suite", required=True, choices=["agent_eval", "explainer"])
    er.add_argument("--pg-host", default="/tmp")
    er.add_argument("--pg-port", type=int, default=55432)
    er.add_argument("--pg-user", default="dq_gov")
    er.add_argument("--db", default="dqcp_demo", help="the control-plane database to record the run into")
    bt = sub.add_parser("backtest")
    bt.add_argument("--pipeline", required=True, help="pipeline id (e.g. tmsis_outbound)")
    bt.add_argument("--cr", required=True, help="the change request id to backtest, e.g. CR-a1b2c3d4e5")
    bt.add_argument("--nights", type=int, required=True, help="nights of --data to run in each sandbox")
    bt.add_argument("--data", required=True, help="a synthetic run directory, e.g. synthetic/out/w1")
    bt.add_argument("--state", default="ZZ")
    bt.add_argument("--pg-host", default="/tmp")
    bt.add_argument("--pg-port", type=int, default=55432)
    bt.add_argument("--pg-user", default="dq_gov")
    bt.add_argument("--db", default="dqcp_demo", help="the LIVE control-plane database (read-only: effective "
                    "items + the CR's own items; the report is written back only through dq_ctl.backtest_put)")
    a = ap.parse_args(argv)
    return {"harness": cmd_harness, "demo": cmd_demo, "eval": cmd_eval, "backtest": cmd_backtest}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
