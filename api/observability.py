"""Observability layer for the M10 backend.

This module is where you (the learner) declare the three Prometheus metric
families and implement the three ASGI middleware classes that the autograder
exercises through the FastAPI app.
"""

import uuid
import time
import json
import logging
from contextvars import ContextVar
from prometheus_client import Counter, Histogram, Gauge

# ContextVar for Request ID
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Logger
logger = logging.getLogger("m11.api")

# Metric Declarations at module scope
requests_total = Counter(
    "requests_total",
    "Total number of HTTP requests processed, labeled by path and status.",
    ["path", "status"]
)

request_latency_seconds = Histogram(
    "request_latency_seconds",
    "HTTP request latency in seconds, labeled by path.",
    ["path"]
)

inflight_requests = Gauge(
    "inflight_requests",
    "Current number of in-flight HTTP requests."
)


class RequestIdMiddleware:
    """ASGI middleware to generate a unique request ID, store it in ContextVar,

    and attach it to the response headers.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Check if X-Request-ID is already present.
                has_request_id = False
                for k, v in headers:
                    if k.lower() == b"x-request-id":
                        has_request_id = True
                        break
                if not has_request_id:
                    headers.append((b"x-request-id", request_id.encode("utf-8")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


class StructuredLoggingMiddleware:
    """ASGI middleware to time the request and emit a single JSON line at INFO level."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_seconds = time.perf_counter() - start_time
            latency_ms = elapsed_seconds * 1000.0

            route = scope.get("route")
            if route and hasattr(route, "path"):
                path = route.path
            else:
                path = scope.get("path", "")

            request_id = request_id_var.get()

            log_line = {
                "request_id": request_id,
                "path": path,
                "status": status_code,
                "latency_ms": latency_ms
            }
            logger.info(json.dumps(log_line))


class MetricsMiddleware:
    """ASGI middleware to track request volumes, latency, and in-flight counts."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inflight_requests.inc()
        start_time = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start_time

            route = scope.get("route")
            if route and hasattr(route, "path"):
                path = route.path
            else:
                path = scope.get("path", "")

            # Observe metrics
            requests_total.labels(path=path, status=str(status_code)).inc()
            request_latency_seconds.labels(path=path).observe(elapsed)
            inflight_requests.dec()
