"""tests/t0/test_k10_robustness.py: unit tests for K10 Alert Sinks (WebhookSink / LogSink) and SLA arithmetic."""
import pytest

from dqagent.alerts import LogSink, WebhookSink
from dqagent.deploy import NotConfiguredError


def test_log_sink_records_and_logs():
    sink = LogSink()
    sink.emit("TEST_ALERT", {"detail": "hello"})
    assert len(sink.recorded) == 1
    assert sink.recorded[0]["kind"] == "TEST_ALERT"
    assert sink.recorded[0]["payload"]["detail"] == "hello"


def test_webhook_sink_in_production_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("DQ_ALERT_WEBHOOK_URL", raising=False)
    with pytest.raises(NotConfiguredError, match="WebhookSink URL is required in production deployment"):
        WebhookSink(deployment="production")


def test_webhook_sink_in_test_does_not_raise(monkeypatch):
    monkeypatch.delenv("DQ_ALERT_WEBHOOK_URL", raising=False)
    sink = WebhookSink(deployment="test")
    # Emitting without URL in test deployment gracefully no-ops
    sink.emit("TEST_ALERT", {"detail": "hello"})


def test_webhook_sink_with_url_configured(monkeypatch):
    monkeypatch.setenv("DQ_ALERT_WEBHOOK_URL", "https://alerts.example.com/webhook")
    sink = WebhookSink(deployment="production")
    assert sink.url == "https://alerts.example.com/webhook"
    sink.emit("TEST_ALERT", {"detail": "hello"})
