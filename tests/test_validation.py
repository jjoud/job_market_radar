"""Tests for source/transformed validation and the quality report."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from job_market_radar.ingestion import CsvDataSource
from job_market_radar.schema import SalaryFlag
from job_market_radar.transformation import (
    build_jobs_table,
    clean_job_postings,
    extract_skills,
    normalize_job_postings,
)
from job_market_radar.validation import (
    DataValidationError,
    Severity,
    build_quality_report,
    validate_source,
    validate_transformed,
    write_quality_report,
)

MakeRaw = Callable[..., pd.DataFrame]


def _processed(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    normalized = normalize_job_postings(clean_job_postings(raw).data)
    extraction = extract_skills(normalized)
    return build_jobs_table(normalized), extraction.skills, extraction.job_skills


def _issue(result, check: str):  # noqa: ANN001, ANN202 - small test helper
    matches = [issue for issue in result.issues if issue.check == check]
    assert matches, f"expected check {check!r} in {[i.check for i in result.issues]}"
    return matches[0]


# =============================================================================
# Source validation
# =============================================================================


class TestSourceValidation:
    def test_clean_source_passes_without_warnings(self, make_raw: MakeRaw) -> None:
        result = validate_source(make_raw({"posting_id": "A"}, {"posting_id": "B"}))
        assert result.passed
        assert result.warnings == []

    def test_missing_required_column_is_error(self, make_raw: MakeRaw) -> None:
        result = validate_source(make_raw({}).drop(columns=["skills", "title"]))
        assert not result.passed
        assert "skills" in _issue(result, "required_columns").message

    def test_empty_source_is_error(self, make_raw: MakeRaw) -> None:
        result = validate_source(make_raw({}).iloc[0:0])
        assert _issue(result, "row_count").severity is Severity.ERROR

    def test_extra_column_is_info(self, make_raw: MakeRaw) -> None:
        result = validate_source(make_raw({}).assign(notes="x"))
        assert result.passed
        assert _issue(result, "extra_columns").severity is Severity.INFO

    def test_missing_id_and_title_are_warnings_other_columns_info(self, make_raw: MakeRaw) -> None:
        result = validate_source(make_raw({"posting_id": "", "title": " ", "city": ""}))
        missing = {i.message.split(" ")[0]: i.severity for i in result.issues if i.check == "missing_values"}
        assert missing == {
            "posting_id": Severity.WARNING,
            "title": Severity.WARNING,
            "city": Severity.INFO,
        }

    def test_duplicates_are_detected(self, make_raw: MakeRaw) -> None:
        result = validate_source(
            make_raw(
                {"posting_id": "A"},
                {"posting_id": "A"},  # exact duplicate
                {"posting_id": "A", "city": "Berlin"},  # same id, different content
            )
        )
        assert _issue(result, "exact_duplicates").affected_rows == 1
        assert _issue(result, "duplicate_posting_id").affected_rows == 2
        assert result.passed  # handled by cleaning, not fatal

    def test_salary_and_date_problems(self, make_raw: MakeRaw) -> None:
        result = validate_source(
            make_raw(
                {"posting_id": "A", "salary_min_usd": "90000", "salary_max_usd": "50000"},
                {"posting_id": "B", "salary_min_usd": "abc"},
                {"posting_id": "C", "salary_max_usd": ""},
                {"posting_id": "D", "posted_on": "05/01/2024"},
            )
        )
        assert _issue(result, "salary_min_greater_than_max").sample_ids == ("A",)
        assert _issue(result, "invalid_salary_value").sample_ids == ("B",)
        assert _issue(result, "partial_salary_range").sample_ids == ("C",)
        assert _issue(result, "invalid_date").sample_ids == ("D",)

    def test_unknown_category_and_suspicious_separator(self, make_raw: MakeRaw) -> None:
        result = validate_source(
            make_raw(
                {"posting_id": "A", "seniority": "principal"},
                {"posting_id": "B", "skills": "python,sql"},
                {"posting_id": "C", "seniority": "Sr"},  # alias -> known
            )
        )
        unknown = _issue(result, "unknown_category")
        assert unknown.sample_ids == ("A",)
        assert _issue(result, "suspicious_skill_separator").sample_ids == ("B",)

    def test_untrimmed_whitespace_is_info(self, make_raw: MakeRaw) -> None:
        result = validate_source(make_raw({"title": " Data Analyst "}))
        assert _issue(result, "untrimmed_whitespace").severity is Severity.INFO

    def test_validation_does_not_modify_input(self, make_raw: MakeRaw) -> None:
        raw = make_raw({"title": " x ", "salary_min_usd": "abc"})
        snapshot = raw.copy()
        validate_source(raw)
        pd.testing.assert_frame_equal(raw, snapshot)


# =============================================================================
# Transformed validation
# =============================================================================


class TestTransformedValidation:
    def test_valid_outputs_pass(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({"posting_id": "A"}, {"posting_id": "B"}))
        result = validate_transformed(jobs, skills, job_skills, expected_titles={"Data Analyst"})
        assert result.passed, result.errors
        assert result.warnings == []

    def test_missing_output_column_is_error(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        result = validate_transformed(jobs.drop(columns=["role_family"]), skills, job_skills)
        assert _issue(result, "required_columns").severity is Severity.ERROR

    def test_duplicate_posting_id_is_error(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        result = validate_transformed(pd.concat([jobs, jobs]), skills, job_skills)
        assert not result.passed
        assert result.has_check("unique_posting_id")

    def test_wrong_dtypes_are_errors(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        broken = jobs.assign(posted_on=jobs["posted_on"].astype(str), salary_min_usd=jobs["salary_min_usd"].astype(float))
        result = validate_transformed(broken, skills, job_skills)
        assert len([i for i in result.errors if i.check == "data_type"]) == 2

    def test_salary_min_greater_than_max_is_error(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        jobs.loc[0, "salary_min_usd"] = 200000
        result = validate_transformed(jobs, skills, job_skills)
        assert _issue(result, "salary_min_greater_than_max").severity is Severity.ERROR

    def test_zero_salary_and_wrong_currency_are_errors(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        jobs.loc[0, "salary_min_usd"] = 0
        jobs.loc[0, "salary_currency"] = "EUR"
        result = validate_transformed(jobs, skills, job_skills)
        assert result.has_check("salary_non_positive")
        assert result.has_check("salary_currency")

    def test_salary_flag_consistency(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({"salary_min_usd": "", "salary_max_usd": ""}))
        jobs.loc[0, "salary_quality_flag"] = SalaryFlag.OK
        result = validate_transformed(jobs, skills, job_skills)
        assert _issue(result, "salary_flag_consistency").severity is Severity.ERROR

    def test_handled_salary_problems_are_warnings(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(
            make_raw(
                {"posting_id": "A", "salary_min_usd": "9", "salary_max_usd": "1"},
                {"posting_id": "B", "salary_max_usd": ""},
                {"posting_id": "C", "salary_min_usd": "", "salary_max_usd": ""},
            )
        )
        result = validate_transformed(jobs, skills, job_skills)
        assert result.passed
        assert _issue(result, "salary_invalid_range").severity is Severity.WARNING
        assert _issue(result, "salary_partial").severity is Severity.WARNING
        assert _issue(result, "salary_missing").severity is Severity.INFO

    def test_missing_expected_title_is_error(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        result = validate_transformed(
            jobs, skills, job_skills, expected_titles={"Data Analyst", "Analytics Engineer"}
        )
        issue = _issue(result, "title_coverage")
        assert issue.severity is Severity.ERROR
        assert "Analytics Engineer" in issue.message

    def test_title_seniority_mismatch_is_warning(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({"title": "Senior Data Analyst", "seniority": "junior"}))
        assert _issue(validate_transformed(jobs, skills, job_skills), "title_seniority_mismatch").severity is Severity.WARNING

    def test_unmapped_role_family_is_info(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({"title": "Data Steward"}))
        result = validate_transformed(jobs, skills, job_skills)
        assert result.passed
        assert _issue(result, "unmapped_role_family").severity is Severity.INFO

    def test_skill_dimension_errors(self, make_raw: MakeRaw) -> None:
        jobs, _, job_skills = _processed(make_raw({}))
        skills = pd.DataFrame(
            {"skill_key": ["sql", "excel", "Excel "], "skill_name": ["SQL", "Excel", "excel"]}
        )
        result = validate_transformed(jobs, skills, job_skills)
        assert result.has_check("unique_skill_name")
        assert result.has_check("skill_key_normalized")

    def test_referential_integrity_errors(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({"posting_id": "A"}))
        bad_bridge = pd.concat(
            [
                job_skills,
                job_skills.head(1),  # duplicate pair
                pd.DataFrame({"posting_id": ["ZZZ"], "skill_key": ["sql"]}),
                pd.DataFrame({"posting_id": ["A"], "skill_key": ["cobol"]}),
            ],
            ignore_index=True,
        )
        result = validate_transformed(jobs, skills, bad_bridge)
        for check in ("unique_job_skill", "job_skills_fk_posting", "job_skills_fk_skill"):
            assert _issue(result, check).severity is Severity.ERROR

    def test_posting_without_skills_is_warning(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({"posting_id": "A"}, {"posting_id": "B", "skills": ""}))
        result = validate_transformed(jobs, skills, job_skills)
        assert result.passed
        assert _issue(result, "posting_without_skills").sample_ids == ("B",)

    def test_data_validation_error_message(self, make_raw: MakeRaw) -> None:
        jobs, skills, job_skills = _processed(make_raw({}))
        result = validate_transformed(pd.concat([jobs, jobs]), skills, job_skills)
        with pytest.raises(DataValidationError, match="unique_posting_id"):
            raise DataValidationError(result)


# =============================================================================
# Quality report
# =============================================================================


def _report_for(path: Path):  # noqa: ANN202
    ingestion = CsvDataSource(path).read()
    source_result = validate_source(ingestion.data)
    cleaning = clean_job_postings(ingestion.data)
    normalized = normalize_job_postings(cleaning.data)
    jobs = build_jobs_table(normalized)
    extraction = extract_skills(normalized)
    transformed_result = validate_transformed(jobs, extraction.skills, extraction.job_skills)
    return build_quality_report(
        ingestion=ingestion,
        cleaning=cleaning,
        jobs=jobs,
        extraction=extraction,
        validations=[source_result, transformed_result],
    )


class TestQualityReport:
    def test_report_contains_required_metrics(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "dirty.csv"
        csv_path.write_text(
            "posting_id,posted_on,title,seniority,city,country,remote,salary_min_usd,salary_max_usd,company_size,skills\n"
            "A,2024-01-01,Data Analyst,mid,X,US,remote,10,20,startup,sql|SQL\n"
            "A,2024-01-01,Data Analyst,mid,X,US,remote,10,20,startup,sql|SQL\n"
            ",2024-01-01,Data Engineer,mid,X,US,remote,,,startup,python\n"
            "B,2024-01-02,Data Engineer,mid,X,US,remote,,,startup,Python|Power BI\n",
            encoding="utf-8",
        )
        report = _report_for(csv_path)

        assert report.row_counts["raw_rows"] == 4
        assert report.row_counts["cleaned_rows"] == 2
        assert report.row_counts["rejected_rows"] == 2
        assert report.rejection_reasons["exact_duplicate"] == 1
        assert report.rejection_reasons["missing_posting_id"] == 1
        assert report.duplicates["duplicate_skills_within_posting"] == 1
        assert report.missing_values_raw["salary_min_usd"] == 2
        assert report.missing_values_processed["salary_min_usd"] == 1
        assert report.skills["unique_skills"] == 3
        assert report.salary_quality == {
            "ok": 1, "missing": 1, "partial": 0, "invalid_value": 0, "invalid_range": 0
        }
        assert report.passed

    def test_report_is_written_as_json_and_markdown(self, sample_csv: Path, tmp_path: Path) -> None:
        report = _report_for(sample_csv)
        json_path, markdown_path = write_quality_report(report, tmp_path / "reports")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["row_counts"]["raw_rows"] == 5
        assert data["source"]["sha256"]
        assert len(data["title_mapping"]) == 5

        markdown = markdown_path.read_text(encoding="utf-8")
        for heading in ("## Row counts", "## Rejected records", "## Missing values", "## Skills extraction", "## Validation results"):
            assert heading in markdown
        assert "suspicious_skill_separator" in markdown  # "python|pytorch,aws" in fixture
