"""Tenant profile: who the agent serves, for display and for approver organisation.

The repository stays generic (no client names; tests/t0/test_generic_naming.py). A real deployment or demo supplies
its tenant profile from OUTSIDE the repository, via the DQ_TENANT_PROFILE environment variable or the CLI
`--tenant` option. config/tenant.example.yaml documents the shape with generic values.

  display_name / agency / division   shown in the Tower header
  organisations                      {ORG_CODE: label}, e.g. STATE (the agency) and VENDOR (implementation partner)
  people                             {person_id: ORG_CODE}: the organisation each roster person belongs to
  governance.approver_org            configuration changes must be approved by a person of this organisation
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

GENERIC = {"display_name": "Demo tenant", "agency": "State Medicaid agency", "division": None,
           "organisations": {"STATE": "State agency", "VENDOR": "Implementation partner"},
           "people": {}, "governance": {"approver_org": None}, "x_source": "generic default"}


def load_tenant(path: str | None = None) -> dict:
    p = path or os.environ.get("DQ_TENANT_PROFILE")
    if not p:
        return dict(GENERIC)
    with open(Path(p)) as f:
        t = yaml.safe_load(f) or {}
    out = {**GENERIC, **t, "x_source": "tenant profile"}
    out["governance"] = {**GENERIC["governance"], **(t.get("governance") or {})}
    return out


def public_view(t: dict) -> dict:
    return {k: t.get(k) for k in ("display_name", "agency", "division", "organisations")} | \
        {"approver_org": t["governance"].get("approver_org"), "source": t.get("x_source")}
