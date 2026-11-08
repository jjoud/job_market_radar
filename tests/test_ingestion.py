"""Tests for the ingestion layer (CSV source and DataSource contract)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from job_market_radar.ingestion import (
    CsvDataSource,
    DataSource,
    DataSourceError,
    compute_file_sha256,
)

EXPECTED_COLUMNS = (
    "posting_id",
    "posted_on",
    "title",
    "seniority",
    "city",
    "country",
    "remote",
    "salary_min_usd",
    "salary_max_usd",
    "company_size",
    "skills",
)


# --- Happy path -----------------------------------------------------------


def test_reads_all_records_and_columns(sample_csv: Path) -> None:
    result = CsvDataSource(sample_csv).read()

    assert result.record_count == 5
    assert len(result.data) == 5
    assert result.columns == EXPECTED_COLUMNS
    assert result.source_name == "csv"
    assert result.source_location == str(sample_csv)


def test_values_are_kept_raw_as_strings(sample_csv: Path) -> None:
    data = CsvDataSource(sample_csv).read().data

    assert all(dtype == object for dtype in data.dtypes)
    # Missing salary stays an empty string, not NaN.
    assert data.loc[1, "salary_min_usd"] == ""
    assert not data.isna().any().any()
    # Whitespace is preserved; cleaning is not ingestion's job.
    assert data.loc[3, "title"] == "  Junior Data Analyst "
    assert data.loc[3, "skills"] == "sql| Python |power bi"
    # Numeric-looking values are not converted.
    assert data.loc[0, "salary_min_usd"] == "70000"


def test_non_ascii_and_quoted_fields(sample_csv: Path) -> None:
    data = CsvDataSource(sample_csv).read().data

    assert data.loc[2, "city"] == "São Paulo"
    assert data.loc[4, "skills"] == "python|pytorch,aws"


def test_metadata_contains_checksum(sample_csv: Path) -> None:
    result = CsvDataSource(sample_csv).read()

    assert result.metadata["sha256"] == compute_file_sha256(sample_csv)
    assert result.metadata["file_name"] == sample_csv.name
    assert int(result.metadata["file_size_bytes"]) == sample_csv.stat().st_size
    assert result.ingested_at.tzinfo is not None


def test_raw_file_is_not_modified(sample_csv: Path) -> None:
    checksum_before = compute_file_sha256(sample_csv)
    mtime_before = sample_csv.stat().st_mtime_ns

    result = CsvDataSource(sample_csv).read()
    result.data.loc[0, "title"] = "mutated in memory"

    assert compute_file_sha256(sample_csv) == checksum_before
    assert sample_csv.stat().st_mtime_ns == mtime_before


def test_utf8_bom_is_stripped_from_header(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿posting_id,title\nA1,Data Analyst\n".encode("utf-8"))

    assert CsvDataSource(path).read().columns == ("posting_id", "title")


def test_custom_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "semicolon.csv"
    path.write_text("posting_id;title\nA1;Data Analyst\n", encoding="utf-8")

    data = CsvDataSource(path, delimiter=";").read().data
    assert data.loc[0, "title"] == "Data Analyst"


def test_header_only_file_returns_zero_records(tmp_path: Path) -> None:
    path = tmp_path / "header_only.csv"
    path.write_text(",".join(EXPECTED_COLUMNS) + "\n", encoding="utf-8")

    result = CsvDataSource(path).read()
    assert result.record_count == 0
    assert result.columns == EXPECTED_COLUMNS


# --- Error handling -------------------------------------------------------


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DataSourceError, match="not found"):
        CsvDataSource(tmp_path / "missing.csv").read()


def test_directory_path_raises(tmp_path: Path) -> None:
    with pytest.raises(DataSourceError, match="not a file"):
        CsvDataSource(tmp_path).read()


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.touch()
    with pytest.raises(DataSourceError, match="empty"):
        CsvDataSource(path).read()


def test_whitespace_only_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "blank.csv"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(DataSourceError, match="no header"):
        CsvDataSource(path).read()


def test_malformed_row_raises(tmp_path: Path) -> None:
    path = tmp_path / "malformed.csv"
    path.write_text("a,b\n1,2\n3,4,5,6\n", encoding="utf-8")
    with pytest.raises(DataSourceError, match="Malformed"):
        CsvDataSource(path).read()


def test_wrong_encoding_raises(tmp_path: Path) -> None:
    path = tmp_path / "latin1.csv"
    path.write_bytes("city\nS\xe3o Paulo\n".encode("latin-1"))
    with pytest.raises(DataSourceError, match="decode"):
        CsvDataSource(path).read()


# --- Extensibility contract -----------------------------------------------


class _InMemorySource(DataSource):
    """Stand-in for a future API source: only ``location`` and ``_extract`` are needed."""

    source_name = "memory"

    def __init__(self, payload: object) -> None:
        self._payload = payload

    @property
    def location(self) -> str:
        return "memory://test"

    def _extract(self) -> pd.DataFrame:
        return self._payload  # type: ignore[return-value]


def test_custom_source_plugs_into_base_contract() -> None:
    result = _InMemorySource(pd.DataFrame({"posting_id": ["X1", "X2"]})).read()

    assert result.record_count == 2
    assert result.source_name == "memory"
    assert result.metadata == {}


def test_source_returning_non_dataframe_raises() -> None:
    with pytest.raises(DataSourceError, match="must return a pandas DataFrame"):
        _InMemorySource([{"posting_id": "X1"}]).read()


def test_read_logs_record_count(sample_csv: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    logger = logging.getLogger("job_market_radar")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="job_market_radar"):
            CsvDataSource(sample_csv).read()
    finally:
        logger.removeHandler(caplog.handler)

    assert "Read 5 records with 11 columns" in caplog.text
