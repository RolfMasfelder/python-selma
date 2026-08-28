# ============================================================
# test_unit_test_helper.py
#
# Unit tests für selma/test_helper.py (setup_logger).
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import logging
from unittest.mock import patch

from selma import test_helper


def test_setup_logger_basic_configured():
    """Logger bekommt Level, propagate aus, genau ein StreamHandler."""
    name = "test_helper.unit.basic"
    with patch.object(test_helper.tracing, "otel_handler", None):
        test_helper.setup_logger(name)

    log = logging.getLogger(name)
    assert log.level == logging.DEBUG
    assert log.propagate is False
    handlers = [h for h in log.handlers if isinstance(h, logging.StreamHandler)]
    assert len(handlers) == 1

    fmt = handlers[0].formatter
    assert fmt is not None
    # Format-String aus dem Source-Code übernehmen
    expected = "%(asctime)s %(levelname)-8s | %(message)s"
    assert fmt._fmt == expected
    assert fmt.datefmt == "%H:%M:%S"
    log.handlers.clear()


def test_setup_logger_custom_level():
    name = "test_helper.unit.level"
    with patch.object(test_helper.tracing, "otel_handler", None):
        test_helper.setup_logger(name, level=logging.WARNING)
    log = logging.getLogger(name)
    assert log.level == logging.WARNING
    log.handlers.clear()


def test_setup_logger_adds_otel_handler_when_present():
    """Wenn tracing.otel_handler gesetzt ist, wird er dem Logger hinzugefügt."""
    name = "test_helper.unit.otel"
    fake_otel = logging.StreamHandler()
    with patch.object(test_helper.tracing, "otel_handler", fake_otel):
        test_helper.setup_logger(name)
    log = logging.getLogger(name)
    stream_handlers = [h for h in log.handlers if isinstance(h, logging.StreamHandler)]
    assert fake_otel in stream_handlers
    log.handlers.clear()
