"""Bounded file logging with upload keys removed, including from tracebacks."""

import logging
import re
from logging.handlers import RotatingFileHandler

from .paths import app_dir

_KEY_RE = re.compile(r"vwf1_[A-Za-z0-9_-]{43}")


class KeyRedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = _KEY_RE.sub("vwf1_***", record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = _KEY_RE.sub("vwf1_***", logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = _KEY_RE.sub("vwf1_***", record.exc_text)
        if record.stack_info:
            record.stack_info = _KEY_RE.sub("vwf1_***", record.stack_info)
        return True


def configure_logging(*, env=None):
    folder = app_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("valo_uploader")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for old in list(logger.handlers):
        old.close()
        logger.removeHandler(old)
    handler = RotatingFileHandler(folder / "uploader.log", maxBytes=1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.addFilter(KeyRedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
