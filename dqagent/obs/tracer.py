"""Tracer (Part F P1-1 B, F-05 backend): run roots and spans with DETERMINISTIC ids, written as dq_trace_svc only.

    root(kind, trace_id, *, cp, state, ...)   a run root (NIGHT, PUBLISH_CYCLE, DECISION, APPLY_CHANGE, RECOVER, ...)
    span(name, *, seat, subject, authorities) a step inside the current root (a no-op when there is none)
    span_id_for(trace_id, parent, name, subject, ordinal) -> sha256(...)[:16]
    current() -> (trace_id, span_id)          what CP.connect_as stamps into dq.trace_id / dq.span_id

A non-deferred root writes `runs` + run_events(STARTED) at entry; its spans, release checks and guardrail events are
buffered and flushed in ONE transaction at exit with COMPLETED or FAILED. A DEFERRED root (I9: the publish cycle)
writes nothing at entry and flushes at exit only when the cycle changed something (or failed): an idle worker cycle
writes zero rows.

Tracing only observes (I-8): nothing here is read by the engine, ids are deterministic, and a failed flush is logged
and counted (STATS["flush_failures"]), never raised into the operation it observed. DQ_TRACE=0 turns it off (the I-8
test only). Attribute values are counts, booleans and short identifiers only (dq_ctl.span_attrs_ok); a call site
that passes anything else raises ValueError at once, so a bad attribute is a bug found by the tests, not a lost trace.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import functools
import hashlib
import json
import logging
import os
import re
from pathlib import Path

from dqagent.cp.trace_schema import ATTR_KEY, ATTR_VOCAB, COUNT_KEY, REF_RE, SPAN_ID_RE, SPAN_NAME_RE, TRACE_ID_RE

log = logging.getLogger("dqagent.tracer")
_TRACE_RE, _SPAN_RE, _REF_RE = re.compile(TRACE_ID_RE), re.compile(SPAN_ID_RE), re.compile(REF_RE)
_ATTR_KEY, _COUNT_KEY, _NAME_RE = re.compile(ATTR_KEY), re.compile(COUNT_KEY), re.compile(SPAN_NAME_RE)
_SHA = re.compile(r"^[0-9a-f]{64}$")
STATS = {"flush_failures": 0, "last_error": None, "degraded": 0, "last_degraded": None}
_STACK: contextvars.ContextVar[tuple] = contextvars.ContextVar("dq_trace_stack", default=())
ROOT_DIR = Path(__file__).resolve().parents[2]
HASHED = ["dqagent", "rules", "config", "pipelines/w1_dbt/models", "tests"]


def enabled(deployment: str | None = None) -> bool:
    """Tracing is always on, except that DQ_TRACE=0 switches it off in the TEST deployment only (the I-8 test). In any
    other deployment the switch is ignored (and said so in the log): a pilot cannot run untraced by an env var."""
    if os.environ.get("DQ_TRACE", "1") != "0":
        return True
    dep = deployment or os.environ.get("DQ_DEPLOYMENT") or "production"
    if dep == "test":
        return False
    if not STATS.get("_off_refused"):
        STATS["_off_refused"] = True
        log.warning("DQ_TRACE=0 ignored outside the test deployment (%s)", dep)
    return True


def _degrade(where: str, e: Exception) -> None:
    """The trace store could not be read or written (C1: e.g. dq_trace_svc cannot log in). The observed operation
    continues untraced; the degradation is counted and logged (the trace store itself may be the thing that is down,
    so no guardrail row can be relied on here)."""
    STATS["degraded"] += 1
    STATS["last_degraded"] = f"{where}: {type(e).__name__}"[:300]
    log.warning("tracing degraded at %s: %s: %s", where, type(e).__name__, str(e).splitlines()[0] if str(e) else "")


@functools.lru_cache(maxsize=1)
def code_sha256() -> str:
    """The running code version: the ONE definition of the code-tree hash (tools/receipt.code_hash delegates here),
    so a receipt, an eval attestation and every correlated row name the same version."""
    h = hashlib.sha256()
    for top in HASHED:
        for p in sorted((ROOT_DIR / top).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".yaml", ".yml", ".sql"):
                rel = p.relative_to(ROOT_DIR).as_posix()
                content = p.read_bytes().replace(b"\r\n", b"\n")
                h.update(rel.encode() + b"\0" + content + b"\0")
    return h.hexdigest()


def span_id_for(trace_id: str, parent: str | None, name: str, subject: str | None, ordinal: int) -> str:
    return hashlib.sha256(f"{trace_id}|{parent or ''}|{name}|{subject or ''}|{ordinal}".encode()).hexdigest()[:16]


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _check_value(k: str, v):
    """Numbers, booleans, counts, or a string from the CLOSED vocabulary of that key (trace_schema.ATTR_VOCAB): no
    free text, no identifier, no e-mail address can reach a span attribute (I5)."""
    if not _ATTR_KEY.fullmatch(k):
        raise ValueError(f"span attribute key {k!r} is not [a-z][a-z0-9_.]*")
    if isinstance(v, bool) or (isinstance(v, (int, float)) and not isinstance(v, bool)):
        return v
    if isinstance(v, str):
        if v in ATTR_VOCAB.get(k, ()):
            return v
        raise ValueError(f"span attribute {k!r}: {v!r} is not in its closed vocabulary {sorted(ATTR_VOCAB.get(k, ()))}")
    if isinstance(v, dict) and len(v) <= 64 and all(_COUNT_KEY.fullmatch(str(a)) and isinstance(b, int)
                                                     and not isinstance(b, bool) and b >= 0 for a, b in v.items()):
        return {str(a): b for a, b in v.items()}
    raise ValueError(f"span attribute {k!r}: only numbers, booleans, vocabulary strings or counts "
                     f"(got {type(v).__name__})")


class Span:
    __slots__ = ("span_id", "parent", "ordinal", "name", "seat", "subject", "authorities", "status", "started",
                 "ended", "attrs", "refs")

    def __init__(self, span_id, parent, ordinal, name, seat, subject, authorities):
        self.span_id, self.parent, self.ordinal, self.name = span_id, parent, ordinal, name
        self.seat, self.subject, self.authorities = seat, subject, sorted(set(authorities or ()))
        self.status, self.started, self.ended, self.attrs, self.refs = "OK", _now(), None, {}, []

    def attr(self, key: str, value) -> "Span":
        if len(self.attrs) >= 64 and key not in self.attrs:
            raise ValueError("at most 64 span attributes")
        self.attrs[key] = _check_value(key, value)
        return self

    def authorize(self, authorities) -> "Span":
        """Add authorities learned after the span opened (a batch's units are known only after its seal)."""
        self.authorities = sorted(set(self.authorities) | set(authorities or ()))
        return self

    def ref(self, r: str) -> "Span":
        if not _REF_RE.fullmatch(r):
            raise ValueError(f"span ref {r!r} is not a typed reference")
        if len(self.refs) < 32 and r not in self.refs:
            self.refs.append(r)
        return self


class _NullSpan:
    span_id = None

    def attr(self, key, value):
        _check_value(key, value)              # the same contract with tracing off: a bad attribute is still a bug
        return self

    def ref(self, r):
        return self

    def authorize(self, authorities):
        return self


NULL_SPAN = _NullSpan()


class Root:
    def __init__(self, cp, kind, trace_id, *, state, parent_trace_id, pipeline_id, night_date, person_id, authorities,
                 config_digest, code_digest, profile_digests, deferred, deployment, attempt):
        if not _TRACE_RE.fullmatch(trace_id):
            raise ValueError(f"trace id {trace_id!r} does not match {TRACE_ID_RE}")
        self.cp, self.kind, self.trace_id, self.state = cp, kind, trace_id, state
        self.parent_trace_id, self.pipeline_id, self.night_date, self.person_id = (parent_trace_id, pipeline_id,
                                                                                   night_date, person_id)
        self.authorities = sorted(set(authorities or ()))
        self.config_digest, self.code_digest = config_digest or "none", code_digest or code_sha256()
        self.profile_digests, self.deferred, self.deployment, self.attempt = (profile_digests or {}, deferred,
                                                                              deployment or "unknown", attempt)
        self.spans: list[Span] = []
        self.open: list[Span] = []
        self.ordinals: dict = {}
        self.release_checks: list[tuple] = []
        self.guardrails: list[tuple] = []
        self.changed = False
        self.started = False

    def next_ordinal(self, parent):
        n = self.ordinals.get(parent, 0)
        self.ordinals[parent] = n + 1
        return n

    def current_span(self) -> str | None:
        return self.open[-1].span_id if self.open else None

    def mark_changed(self, flag: bool = True):
        self.changed = self.changed or bool(flag)


class _NullRoot:
    trace_id = None
    changed = False

    def mark_changed(self, flag=True):
        pass

    def current_span(self):
        return None


NULL_ROOT = _NullRoot()


def _top() -> Root | None:
    st = _STACK.get()
    return st[-1] if st else None


def current_root() -> Root | None:
    return _top()


def current() -> tuple[str | None, str | None]:
    r = _top()
    return (r.trace_id, r.current_span()) if r else (None, None)


def _runs_row(r: Root) -> tuple:
    return (r.trace_id, r.kind, r.parent_trace_id, r.state, r.pipeline_id, r.night_date, r.attempt, r.person_id,
            r.config_digest, r.code_digest, json.dumps(r.profile_digests, sort_keys=True), r.deployment,
            list(r.authorities))


_RUNS_INSERT = ("INSERT INTO dq_ctl.runs(trace_id,kind,parent_trace_id,state,pipeline_id,night_date,attempt,person_id,"
                "config_digest,code_digest,profile_digests,deployment,authorities) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)")


def _write_start(r: Root):
    with r.cp.as_role("dq_trace_svc", trace=False) as c:
        c.execute(_RUNS_INSERT, _runs_row(r))
        c.execute("INSERT INTO dq_ctl.run_events(trace_id,event) VALUES (%s,'STARTED')", (r.trace_id,))
    r.started = True


def _flush(r: Root, event: str, error_class: str | None):
    try:
        with r.cp.as_role("dq_trace_svc", trace=False) as c:
            if not r.started:
                c.execute(_RUNS_INSERT, _runs_row(r))
                c.execute("INSERT INTO dq_ctl.run_events(trace_id,event) VALUES (%s,'STARTED')", (r.trace_id,))
            if r.spans:
                c.cursor().executemany(
                    "INSERT INTO dq_ctl.spans(span_id,trace_id,parent_span_id,ordinal,name,seat,subject,authorities,"
                    "status,started_at,ended_at,attrs,refs) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                    [(s.span_id, r.trace_id, s.parent, s.ordinal, s.name, s.seat, s.subject, s.authorities, s.status,
                      s.started, s.ended or s.started, json.dumps(s.attrs, sort_keys=True), s.refs) for s in r.spans])
            if r.release_checks:
                c.cursor().executemany("INSERT INTO dq_ctl.release_checks(span_id,unit_key,ok,reasons,authorities) "
                                       "VALUES (%s,%s,%s,%s,%s)", r.release_checks)
            if r.guardrails:
                c.cursor().executemany("INSERT INTO dq_ctl.guardrail_events(trace_id,span_id,code,subject,seat,"
                                       "authorities,detail) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                                       [(r.trace_id, *g) for g in r.guardrails])
            # F-34: the union of the authorities this root touched (its own, its spans', its checks' and guardrails',
            # and those of every unit whose events carry its trace id), so a scoped reviewer finds the root of a night
            # they took part in and sees only their own in-scope spans of it
            touched = set(r.authorities)
            for s_ in r.spans:
                touched |= set(s_.authorities)
            for rc in r.release_checks:
                touched |= set(rc[4])
            for g in r.guardrails:
                touched |= set(g[4])
            touched |= {x["a"] for x in c.execute(
                "SELECT DISTINCT unnest(u.authorities) AS a FROM dq_ctl.unit_events e JOIN dq_ctl.units u "
                "USING (unit_key) WHERE e.trace_id = %s", (r.trace_id,)).fetchall()}
            if touched:
                c.execute("INSERT INTO dq_ctl.run_scopes(trace_id, authorities) VALUES (%s,%s)",
                          (r.trace_id, sorted(touched)))
            c.execute("INSERT INTO dq_ctl.run_events(trace_id,event,error_class) VALUES (%s,%s,%s)",
                      (r.trace_id, event, error_class))
    except Exception as e:  # noqa: BLE001 - tracing never changes the outcome of what it observes (I-8)
        STATS["flush_failures"] += 1
        STATS["last_error"] = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:300]
        _degrade("flush", e)


def mint(cp, base: str) -> str | None:
    """A trace id that cannot collide, across threads and processes (I4): `<base>-<n>` with n from the database
    sequence dq_ctl.trace_id_seq (atomic; deterministic on a fresh control plane, which the shape digest relies on).
    None when the trace store cannot be reached (C1): the caller then runs untraced (root(None) is a no-op root)."""
    try:
        with cp.as_role("dq_trace_svc", trace=False) as c:
            n = c.execute("SELECT nextval('dq_ctl.trace_id_seq') AS n").fetchone()["n"]
    except Exception as e:  # noqa: BLE001 - observe only
        _degrade("mint", e)
        return None
    tid = f"{base}-{n}"
    return tid if _TRACE_RE.fullmatch(tid) else None


# both names kept for the callers (runtime, app, explain_service); they mint the same way now
unique_trace_id = mint
numbered_trace_id = mint


def count_runs(cp, kind: str, **where) -> int | None:
    q = "SELECT count(*) n FROM dq_ctl.runs WHERE kind = %s" + "".join(f" AND {k} = %s" for k in where)
    try:
        with cp.as_role("dq_trace_svc", trace=False) as c:
            return c.execute(q, (kind, *where.values())).fetchone()["n"]
    except Exception as e:  # noqa: BLE001 - observe only
        _degrade("count_runs", e)
        return None


def last_run(cp, kind: str, **where) -> str | None:
    """The trace id of the latest recorded root of `kind` matching `where` (a resumed night's previous attempt)."""
    q = "SELECT trace_id FROM dq_ctl.runs WHERE kind = %s" + "".join(f" AND {k} = %s" for k in where) + \
        " ORDER BY started_at DESC, trace_id DESC LIMIT 1"
    try:
        with cp.as_role("dq_trace_svc", trace=False) as c:
            r = c.execute(q, (kind, *where.values())).fetchone()
            return r["trace_id"] if r else None
    except Exception as e:  # noqa: BLE001 - observe only
        _degrade("last_run", e)
        return None


def marks(cp) -> dict | None:
    """High-water marks of what a publish cycle can change (I9), read as dq_trace_svc; None when unreachable (C1)."""
    try:
        with cp.as_role("dq_trace_svc", trace=False) as c:
            return dict(c.execute(
                "SELECT coalesce((SELECT max(event_id) FROM dq_ctl.unit_events), 0) AS e, "
                "coalesce((SELECT max(id) FROM dq_ctl.release_checks), 0) AS c, "
                "coalesce((SELECT max(id) FROM dq_ctl.results), 0) AS r, "
                "coalesce((SELECT max(version) FROM dq_ctl.manifest_versions), 0) AS m, "
                "coalesce((SELECT max(item_id) FROM dq_ctl.feed_items), 0) AS f").fetchone())
    except Exception as e:  # noqa: BLE001 - observe only
        _degrade("marks", e)
        return None


def latest_release_checks(cp, unit_keys: list[str]) -> dict:
    """{unit_key: (ok, reasons)} of the last release check recorded for each unit (Publisher: write only changes)."""
    if not unit_keys:
        return {}
    try:
        with cp.as_role("dq_trace_svc", trace=False) as c:
            return {r["unit_key"]: (r["ok"], list(r["reasons"])) for r in c.execute(
                "SELECT DISTINCT ON (unit_key) unit_key, ok, reasons FROM dq_ctl.release_checks "
                "WHERE unit_key = ANY(%s) ORDER BY unit_key, id DESC", (list(unit_keys),)).fetchall()}
    except Exception as e:  # noqa: BLE001 - observe only
        _degrade("latest_release_checks", e)
        return {}


@contextlib.contextmanager
def root(kind: str, trace_id: str | None, *, cp, state: str, parent_trace_id=..., pipeline_id=None, night_date=None,
         person_id=None, authorities=(), config_digest=None, code_digest=None, profile_digests=None, deferred=False,
         deployment=None, attempt=1):
    """A run root. parent_trace_id defaults to the enclosing root (NULL when there is none: standalone). With no id
    (minting failed), tracing disabled, or a start that cannot be written, it is a no-op root (C1: observe only)."""
    if trace_id is None or cp is None or not enabled(deployment):
        yield NULL_ROOT
        return
    outer = _top()
    if parent_trace_id is ...:
        parent_trace_id = outer.trace_id if outer else None
    r = Root(cp, kind, trace_id, state=state, parent_trace_id=parent_trace_id, pipeline_id=pipeline_id,
             night_date=night_date, person_id=person_id, authorities=authorities, config_digest=config_digest,
             code_digest=code_digest, profile_digests=profile_digests, deferred=deferred, deployment=deployment,
             attempt=attempt)
    if not deferred:
        try:
            _write_start(r)
        except Exception as e:  # noqa: BLE001 - observe only: run untraced
            _degrade("start", e)
            yield NULL_ROOT
            return
    token = _STACK.set(_STACK.get() + (r,))
    failed = None
    try:
        yield r
    except BaseException as e:
        failed = type(e).__name__
        raise
    finally:
        _STACK.reset(token)
        if outer is not None and r.changed:
            outer.mark_changed()
        if not deferred or r.changed or failed:
            _flush(r, "FAILED" if failed else "COMPLETED", failed)


def authorize_open(authorities) -> None:
    """Add authorities to every span currently open in the current root (learned mid-span, e.g. after a seal)."""
    r = _top()
    if r is not None:
        for s_ in r.open:
            s_.authorize(authorities)


@contextlib.contextmanager
def span(name: str, *, seat: str | None = None, subject: str | None = None, authorities=()):
    r = _top()
    if r is None:
        yield NULL_SPAN
        return
    if subject is not None and not _REF_RE.fullmatch(subject):
        raise ValueError(f"span subject {subject!r} is not a typed reference")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"span name {name!r} does not match {SPAN_NAME_RE}")
    parent = r.current_span()
    ordinal = r.next_ordinal(parent)
    s = Span(span_id_for(r.trace_id, parent, name, subject, ordinal), parent, ordinal, name, seat, subject, authorities)
    r.open.append(s)
    try:
        yield s
    except BaseException:
        s.status = "ERROR"
        raise
    finally:
        s.ended = _now()
        r.open.pop()
        r.spans.append(s)


def release_check(unit_key: str, ok: bool, reasons: list[str], authorities) -> None:
    """Buffer one release check under the current span (Publisher: only when a unit's result changed)."""
    r = _top()
    if r is None:
        return
    sid = r.current_span()
    if sid is None:
        raise ValueError("a release check is recorded inside a span")
    r.release_checks.append((sid, unit_key, bool(ok), list(reasons), sorted(set(authorities or ()))))
    r.mark_changed()


def guardrail(code: str, subject: str, *, authorities=(), seat: str | None = None, detail: dict | None = None) -> None:
    """Buffer one guardrail event (e.g. FLOOR_APPLIED, CEILING_APPLIED, RECOVERY_REFUSED) under the current span."""
    r = _top()
    if r is None:
        return
    d = _check_value("detail", detail or {}) if detail else {}
    r.guardrails.append((r.current_span(), code, subject, seat, sorted(set(authorities or ())), json.dumps(d,
                                                                                                          sort_keys=True)))


def session_options() -> str | None:
    """`-c dq.trace_id=… -c dq.span_id=… -c dq.code_sha256=…` for CP.connect_as (ids regex-checked first)."""
    opts = []
    tid, sid = current()
    if tid and _TRACE_RE.fullmatch(tid):
        opts.append(f"-c dq.trace_id={tid}")
        if sid and _SPAN_RE.fullmatch(sid):
            opts.append(f"-c dq.span_id={sid}")
    cs = code_sha256()
    if _SHA.fullmatch(cs):
        opts.append(f"-c dq.code_sha256={cs}")
    return " ".join(opts) or None
