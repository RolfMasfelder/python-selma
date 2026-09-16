# phoenix serve
# Phoenix UI: http://localhost:6006

import logging
from typing import Any

from openinference.instrumentation._tracers import OITracer
from openinference.instrumentation.config import TraceConfig
from opentelemetry import trace

# Wrap the global OTel tracer with OpenInference semantics.
# If setup() was called before this module is imported,
# spans are exported to Phoenix. Otherwise the tracer is a no-op.
tracer = OITracer(trace.get_tracer("selma-agent"), TraceConfig())

# Privat gehalten (P1#2, 2026-09-15): externe Consumer lesen den Wert über
# `get_otel_handler()`, Schreiben nur über `set_otel_handler()` (Nutznießer:
# `test_helper.setup_logger` + tests). So bleibt das Modul-Atribut intern —
# `global`-Statement NUR im setter.
_otel_handler: logging.Handler | None = None


def get_otel_handler() -> logging.Handler | None:
    """Aktueller OTel-Log-Handler oder None (wenn setup() nie lief)."""
    return _otel_handler


def set_otel_handler(handler: logging.Handler | None) -> None:
    """OTel-Log-Handler setzen (von setup() bzw. Tests verwendet)."""
    global _otel_handler
    _otel_handler = handler


def add_span_infos(**kwargs: Any) -> None:
    span = trace.get_current_span()
    for key, value in kwargs.items():
        span.set_attribute(key, value)


def trace_and_log(logger: logging.Logger, payload: Any) -> None:
    trace.get_current_span().add_event(str(payload))
    logger.debug("%s", payload)


def setup(project_name: str = "selma-agent", logging_in_terminal: bool = True, endpoint: str | None = None) -> None:
    """Activate Phoenix tracing, auto-instrument OpenAI calls, and bridge
    Python logging into OTel so log records appear as span events in Phoenix.

    Call this once at the very start of your script, before any other imports
    from my_mono, so the tracer picks up the Phoenix provider.

    Without this call the tracer is a no-op and the program runs unchanged.

    Args:
        project_name: Phoenix project name.
        logging_in_terminal: If False, removes the root StreamHandler so logs
            are only exported to Phoenix and not printed to the terminal.
    """
    from openinference.instrumentation.openai import OpenAIInstrumentor
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from phoenix.otel import register

    kwargs: dict[str, Any] = {"project_name": project_name}
    if endpoint:
        kwargs["endpoint"] = endpoint
    register(**kwargs)
    OpenAIInstrumentor().instrument()

    # LoggerProvider without an exporter — log records are captured and
    # attached to the active span context so they appear in Phoenix traces.
    logger_provider = LoggerProvider()
    set_logger_provider(logger_provider)

    # Bridge: forward Python log records into OTel (span context is preserved).
    handler = LoggingHandler(logger_provider=logger_provider)
    set_otel_handler(handler)
    root = logging.getLogger()

    if not logging_in_terminal:
        for h in root.handlers[:]:
            if isinstance(h, logging.StreamHandler):
                root.removeHandler(h)

    root.addHandler(handler)
