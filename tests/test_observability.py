"""YOUR tests for the observability layer.

Per the lab guide, write at least 3 substantive tests, each with at least
1 assertion.
"""

import json
import logging
import re
import pytest
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def _scrape_metrics() -> str:
    return client.get("/metrics").text


def _counter_sample_for(body: str, path: str) -> float:
    """Return the requests_total sample value for the given path label, or 0.0."""
    pattern = re.compile(
        r'^requests_total\{[^}]*path="' + re.escape(path) + r'"[^}]*\}\s+([0-9.eE+-]+)',
        re.MULTILINE,
    )
    m = pattern.search(body)
    return float(m.group(1)) if m else 0.0


def test_request_id_header():
    """Verify that after a request, the X-Request-ID response header is set and non-empty."""
    response = client.get("/healthz")
    request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
    assert request_id is not None, "Response missing X-Request-ID header"
    assert len(request_id) > 0, "X-Request-ID header is empty"


def test_requests_total_counter():
    """Verify that after a request, the requests_total counter for that (path, status) has incremented."""
    before = _counter_sample_for(_scrape_metrics(), "/healthz")
    client.get("/healthz")
    after = _counter_sample_for(_scrape_metrics(), "/healthz")
    assert after >= before + 1, "requests_total counter did not increment after request"


def test_structured_log_contains_request_id(caplog):
    """Verify that the structured log contains the same request_id as the response header."""
    with caplog.at_level(logging.INFO):
        response = client.get("/healthz")

    request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
    assert request_id is not None, "X-Request-ID header is missing"

    found_matching_log = False
    for record in caplog.records:
        msg = record.getMessage()
        try:
            log_data = json.loads(msg)
        except (ValueError, TypeError):
            continue

        if log_data.get("request_id") == request_id:
            found_matching_log = True
            assert log_data.get("path") == "/healthz"
            assert "status" in log_data
            assert "latency_ms" in log_data
            break

    assert found_matching_log, f"Log record with request_id {request_id} not found"
