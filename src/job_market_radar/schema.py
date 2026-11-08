"""Schema constants shared by transformation and validation.

The raw columns below match the actual header of ``data-job-postings.csv``.
"""

from __future__ import annotations

from typing import Final

# --- Raw source -------------------------------------------------------------
RAW_COLUMNS: Final[tuple[str, ...]] = (
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
KEY_COLUMN: Final = "posting_id"
SKILLS_DELIMITER: Final = "|"  # verified against the real file: no other separator is used
DATE_FORMAT: Final = "%Y-%m-%d"
SALARY_COLUMNS: Final[tuple[str, str]] = ("salary_min_usd", "salary_max_usd")
SALARY_CURRENCY: Final = "USD"

# --- Canonical category values ----------------------------------------------
# Ordered from least to most senior as they appear in the data; used for display order.
SENIORITY_LEVELS: Final[tuple[str, ...]] = ("junior", "mid", "senior", "staff", "lead")
WORK_ARRANGEMENTS: Final[tuple[str, ...]] = ("onsite", "hybrid", "remote")
COMPANY_SIZES: Final[tuple[str, ...]] = ("startup", "scaleup", "midmarket", "enterprise")


class SalaryFlag:
    """Values of ``jobs.salary_quality_flag``. Only ``ok`` rows are safe for salary analysis."""

    OK: Final = "ok"
    MISSING: Final = "missing"  # both bounds empty in the source
    PARTIAL: Final = "partial"  # exactly one bound present
    INVALID_VALUE: Final = "invalid_value"  # non-numeric, non-integer or <= 0 (bad side set NULL)
    INVALID_RANGE: Final = "invalid_range"  # min > max in the source (both set NULL)

    ALL: Final[tuple[str, ...]] = (OK, MISSING, PARTIAL, INVALID_VALUE, INVALID_RANGE)


# --- Processed outputs --------------------------------------------------------
JOBS_COLUMNS: Final[tuple[str, ...]] = (
    "posting_id",
    "posted_on",
    "title_raw",
    "title",
    "role_family",
    "seniority",
    "city",
    "country",
    "work_arrangement",
    "company_size",
    "salary_min_usd",
    "salary_max_usd",
    "salary_currency",
    "salary_quality_flag",
)
SKILLS_COLUMNS: Final[tuple[str, ...]] = ("skill_key", "skill_name")
JOB_SKILLS_COLUMNS: Final[tuple[str, ...]] = ("posting_id", "skill_key")
