"""Databricks, Spark, and dbt post-hook script for Medicaid DQ Agent integration."""

import os
import sys
import json
import urllib.request
import urllib.error

DQ_BACKEND_URL = os.environ.get("DQ_BACKEND_URL", "http://localhost:8000")
PIPELINE_ID = os.environ.get("DQ_PIPELINE_ID", "maternal-health-nightly")
BATCH_ID = os.environ.get("DQ_BATCH_ID", "latest")
FAIL_CLOSED = os.environ.get("DQ_FAIL_CLOSED", "true").lower() == "true"


def notify_and_gate(pipeline_id: str, batch_id: str, node_id: str, stats: dict = None) -> dict:
    """Invokes Medicaid DQ Agent validation endpoint directly from Spark/dbt environment."""
    url = f"{DQ_BACKEND_URL.rstrip('/')}/api/evaluations/gate"
    payload = {
        "pipeline_id": pipeline_id,
        "batch_id": batch_id,
        "node_id": node_id,
        "stats": stats or {}
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            print(f"[DQ-AGENT] Validation Response for {pipeline_id} / {node_id}: {result.get('disposition')}")
            
            if FAIL_CLOSED and result.get("disposition") in ("FAIL", "HOLD", "ERROR"):
                print(f"[DQ-AGENT-ALERT] Gate blocked pipeline execution: {result}", file=sys.stderr)
                sys.exit(1)
            return result
    except urllib.error.URLError as e:
        print(f"[DQ-AGENT-ERROR] Unable to reach DQ Agent at {url}: {e}", file=sys.stderr)
        if FAIL_CLOSED:
            sys.exit(2)
        return {"disposition": "ERROR", "message": str(e)}


if __name__ == "__main__":
    node = sys.argv[1] if len(sys.argv) > 1 else "silver"
    notify_and_gate(PIPELINE_ID, BATCH_ID, node)
