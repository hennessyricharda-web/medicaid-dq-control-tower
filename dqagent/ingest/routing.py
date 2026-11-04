"""Inbound file routing driven by configuration (Part F F-02, plan r3 §F-02).

The routing block lives in the state's FEEDS artifact (`feeds.yaml` -> `routing`), so it is governed: a change is a
change request, and the runtime reads the EFFECTIVE one. Shape:

    routing:
      partner_map: {CODE: PARTNER_ID}          # the `partner` group of a match is mapped; an unmapped code passes through
      routes:                                  # list order = same-day arrival order
        - {match: <regex>, format: IE | X12_834 | X12_837P, pipeline: <harness pipeline>, relations: [<contract>...],
           ack: <optional template over the match groups, e.g. 'ack_{partner}_{date}_{seq}.csv'>}
      ignore: [<regex>...]                     # files that are never inbound data (acks, ledgers, manifests)

A file no route matches is UNROUTED: it is never ingested, and it opens one UNROUTED_FILE feed item. There is no
fallback route (the defect this replaces sent every unknown file to claims).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from dqagent.rules.ir import IRError

FORMATS = ("IE", "X12_834", "X12_837P", "DELIMITED")   # what engine.intake can parse (K5: DELIMITED = CSV sources)
UNROUTED_FEED = "UNROUTED"                       # feed-item feed name of an unrouted file
ROUTE_KEYS = {"match", "format", "pipeline", "relations", "ack"}


class Plan(NamedTuple):
    """One routed inbound file: (pipeline, batch B-<stem>, intake files, relations) plus the route's index."""
    pipeline: str
    batch: str
    files: list
    relations: list[str]
    route: int


class Routing:
    def __init__(self, cfg: dict):
        if not isinstance(cfg, dict) or not isinstance(cfg.get("routes"), list) or not cfg["routes"]:
            raise IRError("FEEDS routing: a non-empty `routes` list is required")
        self.partner_map = dict(cfg.get("partner_map") or {})
        self.routes = []
        for i, r in enumerate(cfg["routes"]):
            if not isinstance(r, dict) or set(r) - ROUTE_KEYS or not {"match", "format", "pipeline", "relations"} <= set(r):
                raise IRError(f"FEEDS routing: route {i} needs match, format, pipeline, relations (and optional ack)")
            if r["format"] not in FORMATS:
                raise IRError(f"FEEDS routing: route {i} format {r['format']!r} is not one of {', '.join(FORMATS)}")
            if not isinstance(r["relations"], list) or not r["relations"]:
                raise IRError(f"FEEDS routing: route {i} needs at least one relation")
            try:
                rx = re.compile(r["match"])
            except re.error as e:
                raise IRError(f"FEEDS routing: route {i} match is not a valid regular expression: {e}") from e
            self.routes.append({**r, "_rx": rx})
        try:
            self.ignore = [re.compile(x) for x in (cfg.get("ignore") or [])]
        except re.error as e:
            raise IRError(f"FEEDS routing: an ignore pattern is not a valid regular expression: {e}") from e

    @classmethod
    def of(cls, feeds: dict | None) -> Routing:
        """The routing of a FEEDS artifact's content; a FEEDS without routing is refused (no fallback)."""
        cfg = (feeds or {}).get("routing")
        if cfg is None:
            raise IRError("FEEDS has no `routing` block: inbound files cannot be routed")
        return cls(cfg)

    def validate(self, contracts: dict, harness: dict) -> None:
        """Every route names a harness pipeline and contract relations (registry propose; raises IRError)."""
        pipelines = {p["id"] for p in (harness or {}).get("pipelines", [])}
        rels = set((contracts or {}).get("relations", contracts or {}))
        for i, r in enumerate(self.routes):
            if r["pipeline"] not in pipelines:
                raise IRError(f"FEEDS routing: route {i} pipeline {r['pipeline']!r} is not in the harness")
            unknown = [x for x in r["relations"] if x not in rels]
            if unknown:
                raise IRError(f"FEEDS routing: route {i} relations {unknown} are not in the contracts")

    # -- matching ------------------------------------------------------------------------------------------------
    def ignored(self, name: str) -> bool:
        return any(x.search(name) for x in self.ignore)

    def match(self, name: str) -> tuple[int, re.Match] | None:
        for i, r in enumerate(self.routes):
            m = r["_rx"].search(name)
            if m:
                return i, m
        return None

    def date_of(self, name: str) -> str | None:
        """The delivery date token: the route's `date` group, else any 8-digit token in the name."""
        hit = self.match(name)
        if hit and "date" in hit[1].groupdict() and hit[1].group("date"):
            return hit[1].group("date")
        return next((x for x in re.split(r"[_.\-]", Path(name).stem) if len(x) == 8 and x.isdigit()), None)

    def sort_key(self, p: Path):
        """(date, route index with unrouted last, seq, name): the same-day arrival order."""
        hit = self.match(p.name)
        idx = hit[0] if hit else len(self.routes)
        g = hit[1].groupdict() if hit else {}
        seq = int(g["seq"]) if (g.get("seq") or "").isdigit() else 0
        return (self.date_of(p.name) or "0", idx, seq, p.name)

    def batch_id(self, p: Path) -> str:
        """The batch id of one inbound file (Nit 7). A ROUTED file keeps B-<stem>: its route's anchored pattern fixes
        the extension, and the id is part of every unit key (pinned by the w1 outcome and the agent-eval digests), so
        it must not move. An UNROUTED file uses B-<full name>, so a stray `x.bak` can never share a batch with a
        routed `x.txt`. Any remaining collision inside one delivery is refused by `arrival` (never merged)."""
        return "B-" + (p.stem if self.match(p.name) else p.name)

    def plan(self, data, p: Path) -> Plan | None:
        """The intake plan of one inbound file, or None when no route matches (UNROUTED)."""
        hit = self.match(p.name)
        if hit is None:
            return None
        i, m = hit
        r = self.routes[i]
        g = {k: v for k, v in m.groupdict().items() if v is not None}
        batch = self.batch_id(p)
        if r["format"] == "IE":
            return Plan(r["pipeline"], batch, [("IE", p)], list(r["relations"]), i)
        code = g.get("partner")
        partner = self.partner_map.get(code, code)
        ack = None
        if r.get("ack"):
            try:
                cand = Path(data) / r["ack"].format(**g)
            except KeyError as e:
                raise IRError(f"FEEDS routing: route {i} ack template names a missing group {e}") from e
            ack = cand if cand.exists() else None
        return Plan(r["pipeline"], batch, [(r["format"], p, partner, ack)], list(r["relations"]), i)


def arrival(data, routing: Routing) -> tuple[list[Path], list[Path]]:
    """(inbound files in arrival order, undated unrouted files). Every regular file in `data` that is not ignored is
    inbound: routed files and DATED unrouted files are ordered by Routing.sort_key; an UNDATED unrouted file has no
    night of its own and is returned separately."""
    files = [p for p in sorted(Path(data).iterdir()) if p.is_file() and not routing.ignored(p.name)]
    seen: dict[str, str] = {}
    for p in files:                                   # Nit 7: two inbound files never share a batch silently
        b = routing.batch_id(p)
        if b in seen:
            raise IRError(f"batch id collision: {seen[b]} and {p.name} would both be batch {b}; rename one or "
                          "anchor the routes so they cannot both match")
        seen[b] = p.name
    dated, undated = [], []
    for p in files:
        if routing.match(p.name) is None and routing.date_of(p.name) is None:
            undated.append(p)
        else:
            dated.append(p)
    return sorted(dated, key=routing.sort_key), undated
