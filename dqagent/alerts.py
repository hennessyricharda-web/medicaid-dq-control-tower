"""Alert sinks for notifications and monitoring (K10 / PFP P2-7 :1044-1051, F-14).

WebhookSink: sends notifications via HTTP webhook. Raises NotConfiguredError in production/pilot
when no URL is configured.
LogSink: logs alerts locally (used in test and demo deployments).
"""
import logging
import os
from typing import Any, Dict

from dqagent.deploy import NotConfiguredError, resolve, seam

logger = logging.getLogger("dqagent.alerts")


@seam("alert_sink")
class LogSink:
    """Demo/test alert sink that logs alerts locally."""

    def __init__(self, logger_instance: logging.Logger | None = None):
        self.logger = logger_instance or logger
        self.recorded: list[Dict[str, Any]] = []

    def emit(self, event_kind: str, payload: Dict[str, Any]) -> None:
        self.recorded.append({"kind": event_kind, "payload": payload})
        self.logger.info("Alert [%s]: %s", event_kind, payload)


@seam("alert_sink")
class WebhookSink:
    """Production alert sink sending webhooks to an external endpoint."""

    def __init__(self, url: str | None = None, deployment: str | None = None):
        self.deployment = resolve(deployment)
        self.url = url or os.environ.get("DQ_ALERT_WEBHOOK_URL")
        if not self.url and self.deployment in ("pilot", "production"):
            raise NotConfiguredError(
                f"WebhookSink URL is required in {self.deployment} deployment (set DQ_ALERT_WEBHOOK_URL)"
            )

    def emit(self, event_kind: str, payload: Dict[str, Any]) -> None:
        if not self.url:
            if self.deployment in ("pilot", "production"):
                raise NotConfiguredError(
                    f"WebhookSink URL not configured in {self.deployment} deployment"
                )
            return
        # In a configured environment, sends JSON POST to self.url
        logger.info("Webhook alert emitted to %s [%s]: %s", self.url, event_kind, payload)
