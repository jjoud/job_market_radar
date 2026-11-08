"""Shared pytest fixtures. Tests never touch data/raw or a real database."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from job_market_radar.schema import RAW_COLUMNS

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

DEFAULT_RAW_RECORD: dict[str, str] = {
    "posting_id": "T00001",
    "posted_on": "2024-01-05",
    "title": "Data Analyst",
    "seniority": "mid",
    "city": "New York",
    "country": "US",
    "remote": "remote",
    "salary_min_usd": "70000",
    "salary_max_usd": "95000",
    "company_size": "enterprise",
    "skills": "sql|excel",
}


def build_raw_frame(*overrides: dict[str, str]) -> pd.DataFrame:
    """Raw-style DataFrame (all strings, blanks as "") with one row per override dict."""
    rows = [{**DEFAULT_RAW_RECORD, **override} for override in overrides]
    return pd.DataFrame(rows, columns=list(RAW_COLUMNS)).astype(object)


@pytest.fixture
def make_raw() -> Callable[..., pd.DataFrame]:
    return build_raw_frame


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """Copy of the small sample CSV in a temporary directory."""
    destination = tmp_path / "sample_job_postings.csv"
    shutil.copyfile(FIXTURES_DIR / "sample_job_postings.csv", destination)
    return destination


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove project environment variables so settings tests are deterministic."""
    for name in (
        "RAW_DATA_FILENAME",
        "LOG_LEVEL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch
