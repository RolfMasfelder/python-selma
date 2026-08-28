# ============================================================
# test_unit_tracing.py
#
# Unit tests für selma/tracing.py — ohne Phoenix-Server.
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import logging

from selma import tracing


def test_add_span_infos_without_active_span_is_noop():
    # Außerhalb eines aktiven Spans darf nichts werfen
    tracing.add_span_infos(a=1, b="x")


def test_trace_and_log_outside_span_is_noop(caplog):
    logger = logging.getLogger("tracing.test")
    with caplog.at_level(logging.DEBUG, logger="tracing.test"):
        tracing.trace_and_log(logger, "hallo payload")
    # trace_and_log loggt auf DEBUG-Level
    assert any("hallo payload" in r.message for r in caplog.records)


def test_setup_registers_providers(monkeypatch):
    """setup() ohne Endpoint: initialisiert LoggerProvider + otel_handler."""
    handler_before = tracing.otel_handler
    try:
        tracing.setup(project_name="selma-tests", logging_in_terminal=True, endpoint=None)

        from opentelemetry._logs import get_logger_provider

        assert get_logger_provider() is not None
        assert tracing.otel_handler is not None

        # Root-Logger hat den otel-handler bekommen
        assert any(h is tracing.otel_handler for h in logging.getLogger().handlers)

        # und es funktioniert ohne active span:
        tracing.add_span_infos(k="v", n=7)
        logger = logging.getLogger("tracing.setup.test")
        tracing.trace_and_log(logger, "nach setup")
    finally:
        # Aufräumen: globalen Zustand zurücksetzen
        if tracing.otel_handler is not None:
            root = logging.getLogger()
            if any(h is tracing.otel_handler for h in root.handlers):
                root.removeHandler(tracing.otel_handler)
        # handler_before war vor dem Test — einfach zurücksetzen
        tracing.otel_handler = handler_before
