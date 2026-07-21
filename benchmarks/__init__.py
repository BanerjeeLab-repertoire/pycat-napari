"""PyCAT Validation Suite — a standing per-release regression benchmark.

Run it with ``python -m benchmarks.run_suite``. It is deliberately OUTSIDE ``tests/`` (pytest's
``testpaths``) so it never runs on the per-change loop — the value is the cross-release trend, not a
per-commit gate. The machinery is unit-tested in ``tests/test_validation_suite.py`` (marked ``core``).
"""
