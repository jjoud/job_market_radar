"""Data validation for the raw source and the transformed outputs.

Each check produces a :class:`ValidationIssue` with a severity:

* ``error``   - the data must not move to the next stage (pipeline stops).
* ``warning`` - suspicious data that was handled (e.g. rejected or set NULL).
* ``info``    - descriptive facts for the quality report.

Validation never modifies or drops data; it only reports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import Enum
import logging

import pandas as pd

from job_market_radar.schema import (
    COMPANY_SIZES,
    JOB_SKILLS_COLUMNS,
    JOBS_COLUMNS,
    KEY_COLUMN,
    RAW_COLUMNS,
    SALARY_COLUMNS,
    SALARY_CURRENCY,
    SENIORITY_LEVELS,
    SKILLS_COLUMNS,
    SalaryFlag,
    WORK_ARRANGEMENTS,
)
from job_market_radar.transformation.cleaning import normalize_whitespace, parse_dates, parse_salary
from job_market_radar.transformation.normalization import (
    COMPANY_SIZE_ALIASES,
    SENIORITY_ALIASES,
    WORK_ARRANGEMENT_ALIASES,
    normalize_category,
    parse_title,
)
from job_market_radar.transformation.skills_extractor import normalize_skill_key

SAMPLE_SIZE = 5
SUSPICIOUS_SKILL_SEPARATORS = r"[,;]"
REQUIRED_NON_NULL_SOURCE_COLUMNS = (KEY_COLUMN, "title")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding."""

    check: str
    severity: Severity
    message: str
    affected_rows: int = 0
    sample_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["sample_ids"] = list(self.sample_ids)
        return data


@dataclass
class ValidationResult:
    """All findings of one validation stage."""

    stage: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    @property
    def passed(self) -> bool:
        return not self.errors

    def has_check(self, check: str) -> bool:
        return any(issue.check == check for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def log_summary(self, logger: logging.Logger) -> None:
        """Log every error/warning and a one-line verdict."""
        for issue in self.issues:
            if issue.severity is Severity.ERROR:
                logger.error("[%s] %s: %s", self.stage, issue.check, issue.message)
            elif issue.severity is Severity.WARNING:
                logger.warning("[%s] %s: %s", self.stage, issue.check, issue.message)
        logger.info(
            "%s validation %s (%d errors, %d warnings)",
            self.stage.capitalize(),
            "passed" if self.passed else "FAILED",
            len(self.errors),
            len(self.warnings),
        )


class DataValidationError(Exception):
    """Raised by callers when a validation stage has errors."""

    def __init__(self, result: ValidationResult) -> None:
        details = "; ".join(f"{issue.check}: {issue.message}" for issue in result.errors)
        super().__init__(f"{result.stage} validation failed: {details}")
        self.result = result


# --- helpers -------------------------------------------------------------------


def _sample_ids(frame: pd.DataFrame, mask: pd.Series) -> tuple[str, ...]:
    if KEY_COLUMN not in frame.columns:
        return ()
    ids = frame.loc[mask, KEY_COLUMN].dropna().astype(str).head(SAMPLE_SIZE)
    return tuple(ids)


def _check_mask(
    result: ValidationResult,
    frame: pd.DataFrame,
    mask: pd.Series,
    *,
    check: str,
    severity: Severity,
    message: str,
) -> None:
    """Add an issue when ``mask`` flags at least one row."""
    count = int(mask.sum())
    if count:
        result.issues.append(
            ValidationIssue(
                check=check,
                severity=severity,
                message=f"{message} ({count} rows)",
                affected_rows=count,
                sample_ids=_sample_ids(frame, mask),
            )
        )


def _missing_columns(frame: pd.DataFrame, expected: Iterable[str]) -> list[str]:
    return [column for column in expected if column not in frame.columns]


def _unknown_categories(
    result: ValidationResult,
    frame: pd.DataFrame,
    column: str,
    normalized: pd.Series,
    allowed: Iterable[str],
    severity: Severity,
) -> None:
    allowed_set = set(allowed)
    unknown = normalized.notna() & ~normalized.isin(allowed_set)
    values = sorted(normalized[unknown].unique().tolist())
    _check_mask(
        result,
        frame,
        unknown,
        check="unknown_category",
        severity=severity,
        message=f"{column} has values outside {sorted(allowed_set)}: {values[:SAMPLE_SIZE]}",
    )


# --- source validation -------------------------------------------------------


def validate_source(raw: pd.DataFrame) -> ValidationResult:
    """Validate raw ingested data before any transformation.

    Errors: missing required columns, no records.
    Warnings: blank ids/titles, duplicates, unparseable dates/salaries,
    salary min > max, unknown categories, suspicious skill separators.
    """
    result = ValidationResult(stage="source")

    missing = _missing_columns(raw, RAW_COLUMNS)
    if missing:
        result.issues.append(
            ValidationIssue("required_columns", Severity.ERROR, f"Missing required columns: {missing}")
        )
        return result
    extra = [column for column in raw.columns if column not in RAW_COLUMNS]
    if extra:
        result.issues.append(
            ValidationIssue("extra_columns", Severity.INFO, f"Ignored extra columns: {extra}")
        )
    if raw.empty:
        result.issues.append(ValidationIssue("row_count", Severity.ERROR, "Source has no records"))
        return result

    cleaned = pd.DataFrame({column: normalize_whitespace(raw[column]) for column in RAW_COLUMNS})
    blank = cleaned.isna() | (cleaned == "")

    for column in RAW_COLUMNS:
        severity = Severity.WARNING if column in REQUIRED_NON_NULL_SOURCE_COLUMNS else Severity.INFO
        _check_mask(
            result,
            cleaned,
            blank[column],
            check="missing_values",
            severity=severity,
            message=f"{column} is empty",
        )

    untrimmed = (raw[list(RAW_COLUMNS)].astype("string") != cleaned.astype("string")).fillna(False)
    _check_mask(
        result,
        cleaned,
        untrimmed.any(axis=1),
        check="untrimmed_whitespace",
        severity=Severity.INFO,
        message="Values with leading/trailing or repeated whitespace (will be trimmed)",
    )

    _check_mask(
        result,
        cleaned,
        cleaned.duplicated(keep="first"),
        check="exact_duplicates",
        severity=Severity.WARNING,
        message="Exact duplicate records (after trimming); later copies will be rejected",
    )
    has_id = ~blank[KEY_COLUMN]
    duplicate_id = has_id & cleaned.duplicated(subset=[KEY_COLUMN], keep="first")
    _check_mask(
        result,
        cleaned,
        duplicate_id,
        check="duplicate_posting_id",
        severity=Severity.WARNING,
        message="Repeated posting_id values (first occurrence is kept)",
    )

    _, invalid_dates = parse_dates(cleaned["posted_on"].mask(blank["posted_on"]))
    _check_mask(
        result,
        cleaned,
        invalid_dates,
        check="invalid_date",
        severity=Severity.WARNING,
        message="posted_on is not a valid YYYY-MM-DD date (will be set NULL)",
    )

    min_col, max_col = SALARY_COLUMNS
    parsed: dict[str, pd.Series] = {}
    for column in SALARY_COLUMNS:
        parsed[column], invalid = parse_salary(cleaned[column].mask(blank[column]))
        _check_mask(
            result,
            cleaned,
            invalid,
            check="invalid_salary_value",
            severity=Severity.WARNING,
            message=f"{column} is not a positive whole number",
        )
    _check_mask(
        result,
        cleaned,
        (parsed[min_col] > parsed[max_col]).fillna(False).astype(bool),
        check="salary_min_greater_than_max",
        severity=Severity.WARNING,
        message="salary_min_usd > salary_max_usd",
    )
    _check_mask(
        result,
        cleaned,
        blank[min_col] ^ blank[max_col],
        check="partial_salary_range",
        severity=Severity.WARNING,
        message="Only one salary bound is present",
    )

    for column, aliases, allowed in (
        ("seniority", SENIORITY_ALIASES, SENIORITY_LEVELS),
        ("remote", WORK_ARRANGEMENT_ALIASES, WORK_ARRANGEMENTS),
        ("company_size", COMPANY_SIZE_ALIASES, COMPANY_SIZES),
    ):
        _unknown_categories(
            result,
            cleaned,
            column,
            normalize_category(cleaned[column].mask(blank[column]), aliases),
            allowed,
            Severity.WARNING,
        )

    _check_mask(
        result,
        cleaned,
        cleaned["skills"].str.contains(SUSPICIOUS_SKILL_SEPARATORS, regex=True).fillna(False),
        check="suspicious_skill_separator",
        severity=Severity.WARNING,
        message="skills contain ',' or ';' although '|' is the delimiter (not split)",
    )
    return result


# --- transformed validation -----------------------------------------------


def validate_transformed(
    jobs: pd.DataFrame,
    skills: pd.DataFrame,
    job_skills: pd.DataFrame,
    *,
    expected_titles: Iterable[str] | None = None,
) -> ValidationResult:
    """Validate processed tables before they are saved or loaded.

    Args:
        jobs: Processed jobs table.
        skills: Skills dimension.
        job_skills: Job-skill bridge.
        expected_titles: Cleaned titles that must still be present (e.g. all
            distinct source titles). Any missing title is an error.
    """
    result = ValidationResult(stage="transformed")

    for name, frame, columns in (
        ("jobs", jobs, JOBS_COLUMNS),
        ("skills", skills, SKILLS_COLUMNS),
        ("job_skills", job_skills, JOB_SKILLS_COLUMNS),
    ):
        missing = _missing_columns(frame, columns)
        if missing:
            result.issues.append(
                ValidationIssue("required_columns", Severity.ERROR, f"{name} is missing {missing}")
            )
    if not result.passed:
        return result

    if jobs.empty:
        result.issues.append(ValidationIssue("row_count", Severity.ERROR, "jobs table is empty"))
        return result

    # --- keys
    for column in (KEY_COLUMN, "title_raw", "title", "role_family", "salary_currency", "salary_quality_flag"):
        _check_mask(
            result, jobs, jobs[column].isna(), check="not_null", severity=Severity.ERROR,
            message=f"jobs.{column} is NULL",
        )
    _check_mask(
        result, jobs, jobs[KEY_COLUMN].duplicated(keep=False) & jobs[KEY_COLUMN].notna(),
        check="unique_posting_id", severity=Severity.ERROR, message="posting_id is not unique",
    )

    # --- data types
    if not pd.api.types.is_datetime64_any_dtype(jobs["posted_on"]):
        result.issues.append(
            ValidationIssue("data_type", Severity.ERROR, f"posted_on has dtype {jobs['posted_on'].dtype}, expected datetime64")
        )
    for column in SALARY_COLUMNS:
        if str(jobs[column].dtype) != "Int64":
            result.issues.append(
                ValidationIssue("data_type", Severity.ERROR, f"{column} has dtype {jobs[column].dtype}, expected nullable Int64")
            )
    if not result.passed:
        return result

    # --- salary rules
    min_col, max_col = SALARY_COLUMNS
    min_salary, max_salary = jobs[min_col], jobs[max_col]
    _check_mask(
        result, jobs, (min_salary > max_salary).fillna(False).astype(bool),
        check="salary_min_greater_than_max", severity=Severity.ERROR,
        message="salary_min_usd > salary_max_usd",
    )
    _check_mask(
        result, jobs, ((min_salary <= 0) | (max_salary <= 0)).fillna(False).astype(bool),
        check="salary_non_positive", severity=Severity.ERROR, message="salary <= 0",
    )
    _check_mask(
        result, jobs, jobs["salary_currency"].ne(SALARY_CURRENCY).fillna(True),
        check="salary_currency", severity=Severity.ERROR,
        message=f"salary_currency is not {SALARY_CURRENCY}",
    )
    flag = jobs["salary_quality_flag"]
    both_present = min_salary.notna() & max_salary.notna()
    both_missing = min_salary.isna() & max_salary.isna()
    _check_mask(
        result, jobs, ~flag.isin(SalaryFlag.ALL).fillna(False),
        check="salary_flag_domain", severity=Severity.ERROR, message="unknown salary_quality_flag",
    )
    _check_mask(
        result, jobs, ((flag == SalaryFlag.OK) & ~both_present).fillna(False).astype(bool),
        check="salary_flag_consistency", severity=Severity.ERROR,
        message="flag 'ok' but a salary bound is NULL",
    )
    _check_mask(
        result, jobs, ((flag == SalaryFlag.MISSING) & ~both_missing).fillna(False).astype(bool),
        check="salary_flag_consistency", severity=Severity.ERROR,
        message="flag 'missing' but a salary value is present",
    )
    for salary_flag in (SalaryFlag.PARTIAL, SalaryFlag.INVALID_VALUE, SalaryFlag.INVALID_RANGE):
        _check_mask(
            result, jobs, (flag == salary_flag).fillna(False).astype(bool),
            check=f"salary_{salary_flag}", severity=Severity.WARNING,
            message=f"salary_quality_flag = '{salary_flag}' (excluded from salary analysis)",
        )
    _check_mask(
        result, jobs, (flag == SalaryFlag.MISSING).fillna(False).astype(bool),
        check="salary_missing", severity=Severity.INFO,
        message="Postings without salary (kept as NULL, not 0)",
    )
    _check_mask(
        result, jobs, jobs["posted_on"].isna(),
        check="posted_on_null", severity=Severity.WARNING, message="posted_on is NULL",
    )

    # --- titles and categories
    if expected_titles is not None:
        missing_titles = sorted(set(expected_titles) - set(jobs["title"].dropna()))
        if missing_titles:
            result.issues.append(
                ValidationIssue(
                    "title_coverage", Severity.ERROR,
                    f"{len(missing_titles)} source title(s) missing from jobs: {missing_titles[:SAMPLE_SIZE]}",
                    affected_rows=len(missing_titles),
                )
            )
    parsed_titles = jobs["title"].map(parse_title, na_action="ignore")
    unmapped = parsed_titles.map(lambda item: not item.is_mapped, na_action="ignore").fillna(False).astype(bool)
    _check_mask(
        result, jobs, unmapped,
        check="unmapped_role_family", severity=Severity.INFO,
        message="Titles without a canonical role family (kept with their own name)",
    )
    title_level = parsed_titles.map(lambda item: item.level, na_action="ignore")
    level_mismatch = (title_level.notna() & jobs["seniority"].notna() & (title_level != jobs["seniority"])).astype(bool)
    _check_mask(
        result, jobs, level_mismatch,
        check="title_seniority_mismatch", severity=Severity.WARNING,
        message="Title level prefix disagrees with the seniority column",
    )
    for column, allowed in (
        ("seniority", SENIORITY_LEVELS),
        ("work_arrangement", WORK_ARRANGEMENTS),
        ("company_size", COMPANY_SIZES),
    ):
        _unknown_categories(result, jobs, column, jobs[column], allowed, Severity.WARNING)

    # --- skills dimension
    _check_mask(
        result, skills, skills["skill_key"].isna() | skills["skill_key"].duplicated(keep=False),
        check="unique_skill_key", severity=Severity.ERROR, message="skill_key is NULL or not unique",
    )
    _check_mask(
        result, skills, skills["skill_name"].str.lower().duplicated(keep=False).fillna(False),
        check="unique_skill_name", severity=Severity.ERROR,
        message="skill_name duplicates ignoring case",
    )
    not_canonical = skills["skill_key"].map(normalize_skill_key) != skills["skill_key"]
    _check_mask(
        result, skills, not_canonical.fillna(True),
        check="skill_key_normalized", severity=Severity.ERROR,
        message="skill_key is not in normalised form",
    )

    # --- bridge table
    _check_mask(
        result, job_skills, job_skills.duplicated(subset=list(JOB_SKILLS_COLUMNS), keep=False),
        check="unique_job_skill", severity=Severity.ERROR, message="duplicate (posting_id, skill_key) pairs",
    )
    _check_mask(
        result, job_skills, ~job_skills[KEY_COLUMN].isin(set(jobs[KEY_COLUMN])),
        check="job_skills_fk_posting", severity=Severity.ERROR,
        message="job_skills.posting_id not found in jobs",
    )
    _check_mask(
        result, job_skills, ~job_skills["skill_key"].isin(set(skills["skill_key"])),
        check="job_skills_fk_skill", severity=Severity.ERROR,
        message="job_skills.skill_key not found in skills",
    )
    _check_mask(
        result, skills, ~skills["skill_key"].isin(set(job_skills["skill_key"])),
        check="orphan_skill", severity=Severity.WARNING, message="skills not used by any posting",
    )
    _check_mask(
        result, jobs, ~jobs[KEY_COLUMN].isin(set(job_skills[KEY_COLUMN])),
        check="posting_without_skills", severity=Severity.WARNING,
        message="Postings with no extracted skills (kept)",
    )
    return result
