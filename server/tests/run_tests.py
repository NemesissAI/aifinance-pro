"""Standalone zero-dependency test runner for AIFinance Maintenance Mode test suite.

Can be run directly via:
    python server/tests/run_tests.py
    .venv/Scripts/python.exe server/tests/run_tests.py

Supports both standard library `unittest` discovery (zero dependencies)
and `pytest` execution if installed.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

# Ensure repo root and sub-packages are on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "tools"))
if str(_REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "server"))

# Pre-import conftest to guarantee database isolation before any test discovery
import server.tests.conftest as conftest_module  # noqa: F401


def run_with_unittest(verbose: bool = True) -> int:
    """Run all server/tests test suites using standard library unittest discovery."""
    tests_dir = Path(__file__).resolve().parent
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=str(tests_dir),
        pattern="test_*.py",
        top_level_dir=str(_REPO_ROOT),
    )

    runner = unittest.TextTestRunner(
        verbosity=2 if verbose else 1,
        failfast=False,
    )

    print("=" * 70)
    print("AIFinance Maintenance Mode Test Suite Runner")
    print(f"Directory: {tests_dir}")
    print(f"Total Test Cases Discovered: {suite.countTestCases()}")
    print("=" * 70)

    start_time = time.time()
    result = runner.run(suite)
    duration = time.time() - start_time

    print("-" * 70)
    print(f"Run completed in {duration:.2f}s")
    print(
        f"Tests: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)} | "
        f"Failures: {len(result.failures)} | Errors: {len(result.errors)} | Skipped: {len(result.skipped)}"
    )
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


def run_with_pytest() -> int:
    """Run tests via pytest if installed."""
    try:
        import pytest
        tests_dir = Path(__file__).resolve().parent
        print(f"Running via pytest on {tests_dir}...")
        return pytest.main(["-v", str(tests_dir)])
    except ImportError:
        print("pytest is not installed. Falling back to unittest runner.")
        return run_with_unittest()


def main() -> None:
    use_pytest = "--pytest" in sys.argv
    if use_pytest:
        code = run_with_pytest()
    else:
        verbose = "-q" not in sys.argv
        code = run_with_unittest(verbose=verbose)
    sys.exit(code)


if __name__ == "__main__":
    main()
