"""Shared test fixtures for the edge pipeline. Joint A+B.

Every test here runs without ultralytics or opencv-python installed. If a test
ever needs them, it belongs in a separate suite that is skipped by default -
the whole point of the lazy imports is that this suite stays runnable on a
laptop with nothing but numpy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from edge.calibration.homography import load_calibration  # noqa: E402
from edge.common.config import load_config  # noqa: E402


@pytest.fixture
def cfg():
    """A fresh config per test, so an override in one cannot leak into another."""
    return load_config()


@pytest.fixture
def calib():
    return load_calibration(FIXTURES / "calibration.json")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
