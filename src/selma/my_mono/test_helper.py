import logging

from selma.my_mono import tracing


def setup_logger(name: str, level: int = logging.DEBUG) -> None:
    """Configure a module logger with a clean format, isolated from the root logger."""
    log = logging.getLogger(name)
    log.setLevel(level)
    log.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    log.addHandler(handler)
    if tracing.otel_handler is not None:
        log.addHandler(tracing.otel_handler)
