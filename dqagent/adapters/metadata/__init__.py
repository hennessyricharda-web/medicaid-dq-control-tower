"""Vendor-neutral metadata adapters (Part E §8 B3 and §3.3): open-standard exports and the legacy-totals input.

    assertions.build_assertions(...)  -> rows for GET /api/v1/export/dq-assertions
    lineage.build_lineage(...)        -> OpenLineage-style document for GET /api/v1/export/lineage
    legacy_totals.load / compare      -> parallel-run (legacy vs new) rows for dq_ctl.parallel_run_results
All outputs are deterministic and sorted, built from field allow-lists, with small-cell suppression (1-10).
"""
