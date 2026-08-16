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

otel_handler: logging.Handler | None = None


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
    global otel_handler

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
    otel_handler = LoggingHandler(logger_provider=logger_provider)
    root = logging.getLogger()

    # """
    #     root.addHandler(otel_handler)

    #     if not logging_in_terminal:
    #         for h in root.handlers[:]:
    #             if isinstance(h, logging.StreamHandler) and h is not otel_handler:
    #                 root.removeHandler(h)

    # """

    if not logging_in_terminal:
        for h in root.handlers[:]:
            if isinstance(h, logging.StreamHandler):
                root.removeHandler(h)

    root.addHandler(otel_handler)
