"""Record-level cleaning.

Steps (the input DataFrame is never mutated):

1. Copy the exact original title into ``title_raw``.
2. Collapse internal whitespace runs and trim every raw text column.
3. Turn empty strings into ``<NA>`` (missing is missing, never zero).
4. Reject records that cannot be identified or classified, or are duplicates,
   keeping their original raw values plus a ``rejection_reason``.
5. Convert ``posted_on`` to a date and salaries to nullable integers, and
   assign a ``salary_quality_flag`` to each row.

Records with a bad *optional* value (date, salary) are kept: the bad value is
set to NULL and counted in ``stats`` so the quality report can explain it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

import pandas as pd

from job_market_radar.schema import (
    DATE_FORMAT,
    KEY_COLUMN,
    RAW_COLUMNS,
    SALARY_COLUMNS,
    SALARY_CURRENCY,
    SalaryFlag,
)
from job_market_radar.utils.logger import get_logger

logger = get_logger(__name__)

_WHITESPACE_RUN: Final = re.compile(r"\s+")


class RejectionReason:
    """Reasons a record is excluded from processed outputs."""

    MISSING_POSTING_ID: Final = "missing_posting_id"
    MISSING_TITLE: Final = "missing_title"
    EXACT_DUPLICATE: Final = "exact_duplicate"
    CONFLICTING_DUPLICATE_ID: Final = "duplicate_posting_id_conflicting_content"


REJECTED_COLUMNS: Final[tuple[str, ...]] = (
    "source_row_number",
    "rejection_reason",
    *RAW_COLUMNS,
)


@dataclass(frozen=True)
class CleaningResult:
    """Cleaned records, rejected records (raw values) and counters."""

    data: pd.DataFrame
    rejected: pd.DataFrame
    stats: dict[str, int] = field(default_factory=dict)


def normalize_whitespace(series: pd.Series) -> pd.Series:
    """Trim and collapse whitespace runs to one space; missing values stay missing."""
    return series.astype("string").str.replace(_WHITESPACE_RUN, " ", regex=True).str.strip()


def blank_to_na(series: pd.Series) -> pd.Series:
    """Replace empty strings with ``<NA>``."""
    return series.mask(series == "", pd.NA)


def _rejected_rows(raw: pd.DataFrame, index: pd.Index, reason: str) -> pd.DataFrame:
    """Original raw values of rejected rows with their 1-based file line number."""
    rows = raw.loc[index, list(RAW_COLUMNS)].copy()
    rows.insert(0, "rejection_reason", reason)
    # +2: one for the header line, one because file lines are 1-based.
    rows.insert(0, "source_row_number", raw.index.get_indexer(index) + 2)
    return rows


def parse_dates(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse ``YYYY-MM-DD`` strings.

    Returns:
        Parsed ``datetime64`` series and a boolean mask of present-but-invalid values.
    """
    parsed = pd.to_datetime(series, format=DATE_FORMAT, errors="coerce")
    invalid = series.notna() & parsed.isna()
    return parsed, invalid


def parse_salary(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse a salary column into nullable ``Int64``.

    Valid salaries are finite, whole and strictly positive. Anything else that
    is present in the source is marked invalid and becomes ``<NA>``.

    Returns:
        Parsed ``Int64`` series and a boolean mask of present-but-invalid values.
    """
    numeric = pd.to_numeric(series.astype(object), errors="coerce").astype("float64")
    present = series.notna()
    is_whole_positive = (numeric > 0) & (numeric % 1 == 0)
    invalid = present & ~is_whole_positive.fillna(False)
    valid = numeric.where(present & ~invalid)
    return valid.astype("Int64"), invalid.astype(bool)


def assign_salary_quality(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Convert salary columns and add ``salary_currency`` and ``salary_quality_flag``.

    Rules:
        * both bounds empty            -> ``missing`` (stay NULL, never 0)
        * a bound unparseable / <= 0   -> ``invalid_value`` (that bound set NULL)
        * min > max                    -> ``invalid_range`` (both bounds set NULL)
        * exactly one bound present    -> ``partial`` (present bound kept)
        * otherwise                    -> ``ok``
    """
    out = data.copy()
    min_col, max_col = SALARY_COLUMNS
    min_raw_missing = out[min_col].isna()
    max_raw_missing = out[max_col].isna()

    min_value, min_invalid = parse_salary(out[min_col])
    max_value, max_invalid = parse_salary(out[max_col])

    invalid_value = min_invalid | max_invalid
    invalid_range = (min_value > max_value).fillna(False).astype(bool)
    partial = (min_value.isna() ^ max_value.isna()) & ~invalid_value

    flag = pd.Series(SalaryFlag.OK, index=out.index, dtype="string")
    flag[partial] = SalaryFlag.PARTIAL
    flag[invalid_value] = SalaryFlag.INVALID_VALUE
    flag[invalid_range] = SalaryFlag.INVALID_RANGE
    flag[min_raw_missing & max_raw_missing] = SalaryFlag.MISSING

    out[min_col] = min_value.mask(invalid_range)
    out[max_col] = max_value.mask(invalid_range)
    out["salary_currency"] = pd.Series(SALARY_CURRENCY, index=out.index, dtype="string")
    out["salary_quality_flag"] = flag

    counts = flag.value_counts()
    stats = {f"salary_{name}": int(counts.get(name, 0)) for name in SalaryFlag.ALL}
    return out, stats


def clean_job_postings(raw: pd.DataFrame) -> CleaningResult:
    """Clean raw job postings (see module docstring for the exact rules).

    Args:
        raw: DataFrame from ingestion; all values are raw strings.

    Raises:
        ValueError: If a required raw column is missing (source validation should catch this first).
    """
    missing_columns = [column for column in RAW_COLUMNS if column not in raw.columns]
    if missing_columns:
        raise ValueError(f"Cannot clean data, missing columns: {missing_columns}")

    data = raw.loc[:, list(RAW_COLUMNS)].copy()
    for column in RAW_COLUMNS:
        data[column] = blank_to_na(normalize_whitespace(data[column]))
    data.insert(data.columns.get_loc("title"), "title_raw", raw["title"].astype("string"))

    rejected_parts: list[pd.DataFrame] = []
    stats: dict[str, int] = {"input_rows": len(raw)}

    def reject(mask: pd.Series, reason: str) -> None:
        nonlocal data
        stats[f"rejected_{reason}"] = int(mask.sum())
        if mask.any():
            rejected_parts.append(_rejected_rows(raw, data.index[mask], reason))
            logger.warning("Rejected %d record(s): %s", int(mask.sum()), reason)
        data = data.loc[~mask]

    reject(data[KEY_COLUMN].isna(), RejectionReason.MISSING_POSTING_ID)
    reject(data["title"].isna(), RejectionReason.MISSING_TITLE)
    reject(data.duplicated(subset=list(RAW_COLUMNS), keep="first"), RejectionReason.EXACT_DUPLICATE)
    # Same id, different content: keep the first occurrence in file order.
    reject(data.duplicated(subset=[KEY_COLUMN], keep="first"), RejectionReason.CONFLICTING_DUPLICATE_ID)

    posted_on, invalid_dates = parse_dates(data["posted_on"])
    data = data.assign(posted_on=posted_on)
    stats["posted_on_missing"] = int((posted_on.isna() & ~invalid_dates).sum())
    stats["posted_on_invalid_set_null"] = int(invalid_dates.sum())

    data, salary_stats = assign_salary_quality(data)
    stats.update(salary_stats)

    rejected = (
        pd.concat(rejected_parts).sort_values("source_row_number").reset_index(drop=True)
        if rejected_parts
        else pd.DataFrame(columns=list(REJECTED_COLUMNS))
    )
    stats["cleaned_rows"] = len(data)
    stats["rejected_rows"] = len(rejected)

    logger.info(
        "Cleaning: %d input rows -> %d cleaned, %d rejected",
        stats["input_rows"],
        stats["cleaned_rows"],
        stats["rejected_rows"],
    )
    return CleaningResult(data=data.reset_index(drop=True), rejected=rejected, stats=stats)
