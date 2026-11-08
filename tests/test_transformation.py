"""Tests for cleaning, normalisation and skill extraction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from job_market_radar.ingestion import CsvDataSource
from job_market_radar.schema import JOBS_COLUMNS, SalaryFlag
from job_market_radar.transformation import (
    RejectionReason,
    build_jobs_table,
    clean_job_postings,
    extract_skills,
    normalize_job_postings,
    normalize_skill_key,
    parse_title,
)
from job_market_radar.transformation.normalization import ROLE_FAMILY_ALIASES
from job_market_radar.transformation.skills_extractor import SKILL_ALIASES, SKILL_DISPLAY_NAMES

MakeRaw = Callable[..., pd.DataFrame]


def _transform(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    normalized = normalize_job_postings(clean_job_postings(raw).data)
    extraction = extract_skills(normalized)
    return build_jobs_table(normalized), extraction.skills, extraction.job_skills


# =============================================================================
# Cleaning
# =============================================================================


class TestCleaning:
    def test_title_raw_is_exact_and_title_is_trimmed(self, make_raw: MakeRaw) -> None:
        raw = make_raw({"title": "  Senior   Data Engineer "})
        cleaned = clean_job_postings(raw).data

        assert cleaned.loc[0, "title_raw"] == "  Senior   Data Engineer "
        assert cleaned.loc[0, "title"] == "Senior Data Engineer"

    def test_blank_values_become_na(self, make_raw: MakeRaw) -> None:
        cleaned = clean_job_postings(make_raw({"city": "   ", "skills": ""})).data
        assert pd.isna(cleaned.loc[0, "city"])
        assert pd.isna(cleaned.loc[0, "skills"])

    def test_input_frame_is_not_mutated(self, make_raw: MakeRaw) -> None:
        raw = make_raw({"title": " Data Analyst ", "salary_min_usd": ""})
        snapshot = raw.copy()
        clean_job_postings(raw)
        pd.testing.assert_frame_equal(raw, snapshot)

    def test_rerun_is_deterministic(self, make_raw: MakeRaw) -> None:
        raw = make_raw({"posting_id": "A"}, {"posting_id": "B", "salary_min_usd": ""})
        first = clean_job_postings(raw)
        second = clean_job_postings(raw)
        pd.testing.assert_frame_equal(first.data, second.data)
        assert first.stats == second.stats

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            ({"posting_id": ""}, RejectionReason.MISSING_POSTING_ID),
            ({"posting_id": "   "}, RejectionReason.MISSING_POSTING_ID),
            ({"title": ""}, RejectionReason.MISSING_TITLE),
        ],
    )
    def test_unidentifiable_records_are_rejected(
        self, make_raw: MakeRaw, override: dict[str, str], reason: str
    ) -> None:
        result = clean_job_postings(make_raw({"posting_id": "KEEP"}, override))

        assert result.data["posting_id"].tolist() == ["KEEP"]
        assert result.rejected["rejection_reason"].tolist() == [reason]
        assert result.stats[f"rejected_{reason}"] == 1

    def test_exact_duplicates_keep_first_and_ignore_whitespace(self, make_raw: MakeRaw) -> None:
        result = clean_job_postings(
            make_raw({"posting_id": "A"}, {"posting_id": "A", "city": " New  York "})
        )
        assert len(result.data) == 1
        assert result.rejected["rejection_reason"].tolist() == [RejectionReason.EXACT_DUPLICATE]

    def test_conflicting_duplicate_id_keeps_first_occurrence(self, make_raw: MakeRaw) -> None:
        result = clean_job_postings(
            make_raw({"posting_id": "A", "city": "Berlin"}, {"posting_id": "A", "city": "London"})
        )
        assert result.data["city"].tolist() == ["Berlin"]
        assert result.rejected["rejection_reason"].tolist() == [
            RejectionReason.CONFLICTING_DUPLICATE_ID
        ]

    def test_rejected_rows_keep_original_values_and_line_number(self, make_raw: MakeRaw) -> None:
        raw = make_raw({"posting_id": "A"}, {"posting_id": "", "title": "  Odd Title "})
        rejected = clean_job_postings(raw).rejected

        assert rejected.loc[0, "source_row_number"] == 3  # header is line 1
        assert rejected.loc[0, "title"] == "  Odd Title "

    def test_no_rejections_gives_empty_frame_with_columns(self, make_raw: MakeRaw) -> None:
        rejected = clean_job_postings(make_raw({})).rejected
        assert rejected.empty
        assert {"source_row_number", "rejection_reason", "posting_id"} <= set(rejected.columns)

    def test_missing_column_raises(self, make_raw: MakeRaw) -> None:
        with pytest.raises(ValueError, match="skills"):
            clean_job_postings(make_raw({}).drop(columns=["skills"]))


class TestDatesAndSalaries:
    def test_types_are_converted(self, make_raw: MakeRaw) -> None:
        cleaned = clean_job_postings(make_raw({})).data

        assert pd.api.types.is_datetime64_any_dtype(cleaned["posted_on"])
        assert str(cleaned["salary_min_usd"].dtype) == "Int64"
        assert cleaned.loc[0, "salary_min_usd"] == 70000
        assert cleaned.loc[0, "salary_currency"] == "USD"

    def test_invalid_date_becomes_null_but_record_is_kept(self, make_raw: MakeRaw) -> None:
        result = clean_job_postings(make_raw({"posted_on": "2024-13-45"}))
        assert len(result.data) == 1
        assert pd.isna(result.data.loc[0, "posted_on"])
        assert result.stats["posted_on_invalid_set_null"] == 1

    def test_missing_salary_stays_null_never_zero(self, make_raw: MakeRaw) -> None:
        cleaned = clean_job_postings(make_raw({"salary_min_usd": "", "salary_max_usd": ""})).data

        assert cleaned["salary_min_usd"].isna().all()
        assert cleaned["salary_max_usd"].isna().all()
        assert (cleaned["salary_min_usd"].fillna(-1) != 0).all()
        assert cleaned.loc[0, "salary_quality_flag"] == SalaryFlag.MISSING

    @pytest.mark.parametrize(
        ("salary_min", "salary_max", "flag", "expected_min", "expected_max"),
        [
            ("70000", "95000", SalaryFlag.OK, 70000, 95000),
            ("80000.0", "80000", SalaryFlag.OK, 80000, 80000),
            ("70000", "", SalaryFlag.PARTIAL, 70000, None),
            ("abc", "95000", SalaryFlag.INVALID_VALUE, None, 95000),
            ("0", "95000", SalaryFlag.INVALID_VALUE, None, 95000),
            ("-5", "95000", SalaryFlag.INVALID_VALUE, None, 95000),
            ("70000.5", "95000", SalaryFlag.INVALID_VALUE, None, 95000),
            ("99000", "50000", SalaryFlag.INVALID_RANGE, None, None),
        ],
    )
    def test_salary_quality_rules(
        self,
        make_raw: MakeRaw,
        salary_min: str,
        salary_max: str,
        flag: str,
        expected_min: int | None,
        expected_max: int | None,
    ) -> None:
        row = clean_job_postings(
            make_raw({"salary_min_usd": salary_min, "salary_max_usd": salary_max})
        ).data.iloc[0]

        assert row["salary_quality_flag"] == flag
        for value, expected in ((row["salary_min_usd"], expected_min), (row["salary_max_usd"], expected_max)):
            if expected is None:
                assert pd.isna(value)
            else:
                assert value == expected

    def test_salary_stats_are_counted(self, make_raw: MakeRaw) -> None:
        stats = clean_job_postings(
            make_raw(
                {"posting_id": "A"},
                {"posting_id": "B", "salary_min_usd": "", "salary_max_usd": ""},
                {"posting_id": "C", "salary_min_usd": "9", "salary_max_usd": "1"},
            )
        ).stats
        assert stats["salary_ok"] == 1
        assert stats["salary_missing"] == 1
        assert stats["salary_invalid_range"] == 1


# =============================================================================
# Normalisation
# =============================================================================

FAMILIES = [
    "Data Analyst",
    "Data Engineer",
    "Data Scientist",
    "Analytics Engineer",
    "BI Developer",
    "Machine Learning Engineer",
    "Data Platform Engineer",
    "Research Scientist",
]
LEVELS = {"": None, "Junior ": "junior", "Senior ": "senior", "Staff ": "staff", "Lead ": "lead"}


class TestTitleNormalization:
    @pytest.mark.parametrize("family", FAMILIES)
    @pytest.mark.parametrize(("prefix", "level"), list(LEVELS.items()))
    def test_all_40_dataset_titles_map_to_their_family(
        self, family: str, prefix: str, level: str | None
    ) -> None:
        parsed = parse_title(f"{prefix}{family}")
        assert parsed.role_family == family
        assert parsed.level == level
        assert parsed.is_mapped

    @pytest.mark.parametrize(
        ("title", "family", "level"),
        [
            ("Sr. BI Developer", "BI Developer", "senior"),
            ("jr data analyst", "Data Analyst", "junior"),
            ("Senior ML Engineer", "Machine Learning Engineer", "senior"),
            ("Business Intelligence Developer", "BI Developer", None),
        ],
    )
    def test_title_aliases(self, title: str, family: str, level: str | None) -> None:
        parsed = parse_title(title)
        assert (parsed.role_family, parsed.level) == (family, level)

    @pytest.mark.parametrize(
        ("title", "family", "level"),
        [
            ("Data Steward", "Data Steward", None),
            ("Senior Data Governance Lead", "Data Governance Lead", "senior"),
            ("Lead", "Lead", None),
        ],
    )
    def test_unknown_titles_are_kept_not_collapsed(
        self, title: str, family: str, level: str | None
    ) -> None:
        parsed = parse_title(title)
        assert (parsed.role_family, parsed.level, parsed.is_mapped) == (family, level, False)

    def test_alias_targets_are_canonical_families(self) -> None:
        assert set(ROLE_FAMILY_ALIASES.values()) == set(FAMILIES)

    def test_every_distinct_title_is_preserved(self, make_raw: MakeRaw) -> None:
        titles = [f"{prefix}{family}" for family in FAMILIES for prefix in LEVELS]
        raw = make_raw(*({"posting_id": f"P{i}", "title": t} for i, t in enumerate(titles)))
        jobs, _, _ = _transform(raw)

        assert jobs["title"].nunique() == 40
        assert set(jobs["title"]) == set(titles)
        assert jobs["role_family"].value_counts().eq(5).all()


class TestCategoryNormalization:
    def test_categories_are_normalized(self, make_raw: MakeRaw) -> None:
        raw = make_raw(
            {"posting_id": "A", "seniority": " Sr ", "remote": "On-Site", "company_size": "Mid Market", "country": "us"},
            {"posting_id": "B", "seniority": "Mid-Level", "remote": "HYBRID", "company_size": "startup", "country": "De"},
        )
        jobs, _, _ = _transform(raw)

        assert jobs["seniority"].tolist() == ["senior", "mid"]
        assert jobs["work_arrangement"].tolist() == ["onsite", "hybrid"]
        assert jobs["company_size"].tolist() == ["midmarket", "startup"]
        assert jobs["country"].tolist() == ["US", "DE"]

    def test_unknown_category_is_kept_lowercase(self, make_raw: MakeRaw) -> None:
        jobs, _, _ = _transform(make_raw({"seniority": "Principal"}))
        assert jobs.loc[0, "seniority"] == "principal"

    def test_jobs_table_has_canonical_columns(self, make_raw: MakeRaw) -> None:
        jobs, _, _ = _transform(make_raw({}))
        assert tuple(jobs.columns) == JOBS_COLUMNS
        assert "skills" not in jobs.columns


# =============================================================================
# Skills
# =============================================================================


class TestSkillKeyNormalization:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("power bi", "power bi"),
            ("Power BI", "power bi"),
            ("  POWER   BI ", "power bi"),
            ("Power-BI", "power bi"),
            ("PowerBI", "power bi"),
            ("power_bi", "power bi"),
            ("Microsoft Power BI", "power bi"),
            ("PostgreSQL", "postgresql"),
            ("postgres", "postgresql"),
            ("SQL", "sql"),
            ("k8s", "kubernetes"),
            ("Apache Airflow", "airflow"),
            ("modeling", "modelling"),
            ("Some New Tool", "some new tool"),
        ],
    )
    def test_variants_resolve_to_one_key(self, token: str, expected: str) -> None:
        assert normalize_skill_key(token) == expected

    @pytest.mark.parametrize("token", ["", "   ", None, pd.NA, float("nan")])
    def test_blank_tokens_return_none(self, token: object) -> None:
        assert normalize_skill_key(token) is None

    def test_alias_targets_have_display_names(self) -> None:
        assert set(SKILL_ALIASES.values()) <= set(SKILL_DISPLAY_NAMES)

    def test_display_keys_are_already_normalized(self) -> None:
        for key in SKILL_DISPLAY_NAMES:
            assert normalize_skill_key(key) == key


class TestSkillExtraction:
    def test_splits_on_pipe_and_builds_bridge(self, make_raw: MakeRaw) -> None:
        _, skills, job_skills = _transform(
            make_raw({"posting_id": "A", "skills": "sql|python"}, {"posting_id": "B", "skills": "sql"})
        )
        assert set(skills["skill_key"]) == {"sql", "python"}
        assert sorted(map(tuple, job_skills.values.tolist())) == [
            ("A", "python"),
            ("A", "sql"),
            ("B", "sql"),
        ]

    def test_case_and_alias_variants_become_one_skill(self, make_raw: MakeRaw) -> None:
        extraction = extract_skills(
            normalize_job_postings(
                clean_job_postings(
                    make_raw(
                        {"posting_id": "A", "skills": "Power BI|PostgreSQL"},
                        {"posting_id": "B", "skills": "power bi|postgres"},
                        {"posting_id": "C", "skills": "POWERBI | Postgresql"},
                    )
                ).data
            )
        )
        assert extraction.skills["skill_name"].tolist() == ["PostgreSQL", "Power BI"]
        assert len(extraction.job_skills) == 6
        assert extraction.stats["skill_tokens_resolved_by_alias"] == 2  # postgres, powerbi

    def test_whitespace_empty_tokens_and_duplicates(self, make_raw: MakeRaw) -> None:
        extraction = extract_skills(
            clean_job_postings(make_raw({"skills": " sql || SQL | python | "})).data
        )
        assert extraction.job_skills["skill_key"].tolist() == ["sql", "python"]
        assert extraction.stats["skill_tokens_total"] == 5
        assert extraction.stats["skill_tokens_empty"] == 2
        assert extraction.stats["skill_tokens_duplicate_in_posting"] == 1

    def test_comma_is_not_treated_as_delimiter(self, make_raw: MakeRaw) -> None:
        extraction = extract_skills(clean_job_postings(make_raw({"skills": "python|pytorch,aws"})).data)
        assert extraction.job_skills["skill_key"].tolist() == ["python", "pytorch,aws"]

    def test_posting_without_skills_is_counted_not_dropped(self, make_raw: MakeRaw) -> None:
        jobs, _, job_skills = _transform(
            make_raw({"posting_id": "A"}, {"posting_id": "B", "skills": ""})
        )
        extraction = extract_skills(
            clean_job_postings(make_raw({"posting_id": "A"}, {"posting_id": "B", "skills": ""})).data
        )
        assert set(jobs["posting_id"]) == {"A", "B"}
        assert "B" not in set(job_skills["posting_id"])
        assert extraction.stats["postings_without_skills"] == 1

    def test_custom_delimiter(self, make_raw: MakeRaw) -> None:
        extraction = extract_skills(
            clean_job_postings(make_raw({"skills": "sql;python"})).data, delimiter=";"
        )
        assert set(extraction.skills["skill_key"]) == {"sql", "python"}


# =============================================================================
# End-to-end on the sample fixture
# =============================================================================


def test_sample_fixture_end_to_end(sample_csv: Path) -> None:
    raw = CsvDataSource(sample_csv).read().data
    jobs, skills, job_skills = _transform(raw)

    assert len(jobs) == 5
    assert jobs.loc[jobs["posting_id"] == "T00004", "title_raw"].item() == "  Junior Data Analyst "
    assert jobs.loc[jobs["posting_id"] == "T00004", "title"].item() == "Junior Data Analyst"
    assert jobs.loc[jobs["posting_id"] == "T00003", "city"].item() == "São Paulo"
    assert jobs.loc[jobs["posting_id"] == "T00002", "salary_quality_flag"].item() == SalaryFlag.MISSING
    assert "power bi" in set(skills["skill_key"])
    assert "python" in set(job_skills.loc[job_skills["posting_id"] == "T00004", "skill_key"])
