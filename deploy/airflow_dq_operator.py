"""Apache Airflow / Astronomer / Cloud Composer custom operator for Medicaid DQ Agent gating."""

import json
import logging
from typing import Any, Dict, Optional
import httpx
from airflow.models.baseoperator import BaseOperator
from airflow.exceptions import AirflowException

logger = logging.getLogger(__name__)


class MedicaidDQGateOperator(BaseOperator):
    """Airflow Operator that evaluates a Medicaid data batch through the DQ Agent and enforces gate decisions.

    Attributes:
        dq_api_url (str): Base URL of Medicaid DQ Agent FastAPI backend (e.g. "http://dq-agent.internal:8000").
        pipeline_id (str): Identifier of the pipeline in the DQ catalog (e.g. "maternal-health-nightly").
        batch_id (str): Unique batch identifier or execution date string.
        node_id (str): Target medallion node (e.g. "bronze", "silver", "gold").
        fail_closed (bool): If True, halts DAG execution if DQ score is below threshold or held.
    """

    def __init__(
        self,
        dq_api_url: str,
        pipeline_id: str,
        batch_id: str,
        node_id: str = "silver",
        fail_closed: bool = True,
        api_token: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.dq_api_url = dq_api_url.rstrip("/")
        self.pipeline_id = pipeline_id
        self.batch_id = batch_id
        self.node_id = node_id
        self.fail_closed = fail_closed
        self.api_token = api_token

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        payload = {
            "pipeline_id": self.pipeline_id,
            "batch_id": self.batch_id,
            "node_id": self.node_id,
            "execution_date": context.get("ds"),
        }

        logger.info(f"Submitting DQ gate check for {self.pipeline_id} batch {self.batch_id} at node {self.node_id}")

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.dq_api_url}/api/evaluations/gate",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            disposition = data.get("disposition", "UNKNOWN")
            held = data.get("held", False)
            fingerprint = data.get("fingerprint")

            logger.info(f"DQ Result: Disposition={disposition}, Held={held}, Fingerprint={fingerprint}")

            if self.fail_closed and (held or disposition in ("FAIL", "ERROR", "HOLD")):
                raise AirflowException(
                    f"Medicaid DQ Gate blocked pipeline: disposition={disposition}, held={held}. "
                    f"Inspect unit in Command Tower: {self.dq_api_url}/units/{data.get('unit_key')}"
                )

            return data

        except httpx.HTTPError as err:
            logger.error(f"HTTP error communicating with DQ Agent: {err}")
            if self.fail_closed:
                raise AirflowException(f"Fail-closed: DQ Agent unreachable: {err}")
            return {"disposition": "ERROR", "error": str(err)}
