"""
PyCAT logging
=============
A minimal logging layer that gives PyCAT the benefits of `logging` — level
control, optional redirection to a file for bug reports, the ability to silence
warnings — WITHOUT changing how anything looks by default.

Design goals
------------
- **Zero visible change by default.** Out of the box the logger writes to stdout
  with a plain format, so existing `print("[PyCAT ...] ...")` output looks the
  same. Batch progress narrative stays visible on the console exactly as before.
- **Opt-in verbosity.** ``PYCAT_DEBUG=1`` raises the level to DEBUG (consistent
  with the ``PYCAT_REFINE_DEBUG`` / ``PYCAT_FORCE_CPU`` env-var convention used
  elsewhere).
- **Opt-in file capture.** ``PYCAT_LOG_FILE=/path/to/log`` additionally writes to
  that file, so a user reporting a bug can attach a full run log.
- **No hard dependency churn.** Modules can adopt this incrementally; the batch
  progress prints and other intentional console output can stay as-is.

Usage
-----
    from pycat.utils.logging_utils import get_logger
    log = get_logger(__name__)
    log.info("Loaded %s  shape=%s", name, shape)
    log.warning("Could not read pixel size: %s", err)
    log.debug("verbose detail only shown with PYCAT_DEBUG=1")
"""

import logging
import os
import sys

_CONFIGURED = False
_ROOT_NAME = "pycat"


def _configure_once():
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger(_ROOT_NAME)

    # Level: INFO normally, DEBUG when PYCAT_DEBUG is set.
    debug = os.environ.get("PYCAT_DEBUG", "") not in ("", "0", "false", "False")
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Plain format so default output resembles the previous print() style.
    fmt = logging.Formatter("%(message)s")

    # Console handler → stdout (matches print()), so batch/progress output stays
    # visible on the console exactly as before.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler(stream=sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    # Optional file handler for bug-report capture.
    log_file = os.environ.get("PYCAT_LOG_FILE", "")
    if log_file:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            # File gets a richer format with timestamps and levels.
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"))
            fh.setLevel(logging.DEBUG)
            root.addHandler(fh)
        except Exception:
            # Never let logging setup break the app.
            pass

    # Don't propagate to the Python root logger (avoids duplicate lines if the
    # host app — e.g. napari — also configured logging).
    root.propagate = False
    _CONFIGURED = True


def get_logger(name=None):
    """Return a PyCAT logger. `name` is typically ``__name__``; it is namespaced
    under the shared ``pycat`` root so a single level/handler set controls all of
    PyCAT's logging. Safe to call at import time."""
    _configure_once()
    if not name:
        return logging.getLogger(_ROOT_NAME)
    # Namespace module loggers under the pycat root.
    short = name.split(".")[-1] if name.startswith(_ROOT_NAME) else name
    return logging.getLogger(f"{_ROOT_NAME}.{short}")
