"""Data quality report: collected from every stage, written as JSON and Markdown."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from job_market_radar.ingestion.base import IngestionResult
from job_market_radar.schema import JOBS_COLUMNS, RAW_COLUMNS
from job_market_radar.transformation.cleaning import CleaningResult, RejectionReason
from job_market_radar.transformation.skills_extractor import SkillExtractionResult
from job_market_radar.utils.io import write_json, write_text
from job_market_radar.validation.data_quality import ValidationResult

REPORT_BASENAME = "data_quality_report"


@dataclass
class DataQualityReport:
    """Everything needed to judge whether the processed data can be trusted."""

    generated_at: str
    source: dict[str, str]
    row_counts: dict[str, int]
    rejection_reasons: dict[str, int]
    duplicates: dict[str, int]
    missing_values_raw: dict[str, int]
    missing_values_processed: dict[str, int]
    salary_quality: dict[str, int]
    date_quality: dict[str, int]
    skills: dict[str, int]
    role_families: dict[str, int]
    title_mapping: list[dict[str, object]]
    validation: list[dict[str, object]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(bool(stage["passed"]) for stage in self.validation)

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, **asdict(self)}

    def to_markdown(self) -> str:
        """Render a human-readable report."""
        raw_rows = self.row_counts["raw_rows"] or 1
        lines: list[str] = [
            "# Data Quality Report",
            "",
            f"- **Generated (UTC):** {self.generated_at}",
            f"- **Source file:** `{self.source.get('file_name', '')}`",
            f"- **Source SHA-256:** `{self.source.get('sha256', '')}`",
            f"- **Overall status:** {'PASSED' if self.passed else 'FAILED'}",
            "",
            "> Results describe this dataset only; it may be synthetic and does not",
            "> necessarily represent the real job market.",
            "",
            "## Row counts",
            "",
            "| Metric | Count |",
            "|---|---:|",
            *(f"| {name} | {value:,} |" for name, value in self.row_counts.items()),
            "",
            "## Rejected records",
            "",
            "| Reason | Count |",
            "|---|---:|",
            *(f"| {reason} | {count:,} |" for reason, count in self.rejection_reasons.items()),
            "",
            "Rejected rows (original values + reason) are saved to "
            "`data/processed/rejected_records.csv`.",
            "",
            "## Duplicates",
            "",
            "| Check | Count |",
            "|---|---:|",
            *(f"| {name} | {value:,} |" for name, value in self.duplicates.items()),
            "",
            "## Missing values",
            "",
            "Raw = empty cells in the source file. Processed = NULLs in `jobs.csv`.",
            "",
            "| Column | Raw missing | Raw % |",
            "|---|---:|---:|",
            *(
                f"| {column} | {count:,} | {count / raw_rows:.1%} |"
                for column, count in self.missing_values_raw.items()
            ),
            "",
            "| Processed column | NULLs |",
            "|---|---:|",
            *(f"| {column} | {count:,} |" for column, count in self.missing_values_processed.items()),
            "",
            "## Salary quality (USD, NULL = not provided)",
            "",
            "| salary_quality_flag | Postings |",
            "|---|---:|",
            *(f"| {flag} | {count:,} |" for flag, count in self.salary_quality.items()),
            "",
            "## Dates",
            "",
            *(f"- **{name}:** {value}" for name, value in self.date_quality.items()),
            "",
            "## Skills extraction",
            "",
            "| Metric | Value |",
            "|---|---:|",
            *(f"| {name} | {value:,} |" for name, value in self.skills.items()),
            "",
            "## Role families",
            "",
            "| role_family | Postings |",
            "|---|---:|",
            *(f"| {family} | {count:,} |" for family, count in self.role_families.items()),
            "",
            f"## Title mapping ({len(self.title_mapping)} distinct titles, all preserved)",
            "",
            "| title | role_family | Postings |",
            "|---|---|---:|",
            *(
                f"| {row['title']} | {row['role_family']} | {row['postings']:,} |"
                for row in self.title_mapping
            ),
            "",
            "## Validation results",
            "",
        ]
        for stage in self.validation:
            lines += [
                f"### {str(stage['stage']).capitalize()} validation: "
                f"{'PASSED' if stage['passed'] else 'FAILED'} "
                f"({stage['error_count']} errors, {stage['warning_count']} warnings)",
                "",
            ]
            issues = stage["issues"]
            if not issues:
                lines += ["No issues found.", ""]
                continue
            lines += ["| Severity | Check | Message | Sample IDs |", "|---|---|---|---|"]
            for issue in issues:  # type: ignore[union-attr]
                samples = ", ".join(issue["sample_ids"]) or "-"
                lines.append(
                    f"| {issue['severity']} | {issue['check']} | {issue['message']} | {samples} |"
                )
            lines.append("")
        return "\n".join(lines)


def _raw_missing_counts(raw: pd.DataFrame) -> dict[str, int]:
    return {
        column: int((raw[column].astype("string").str.strip().fillna("") == "").sum())
        for column in RAW_COLUMNS
        if column in raw.columns
    }


def build_quality_report(
    *,
    ingestion: IngestionResult,
    cleaning: CleaningResult,
    jobs: pd.DataFrame,
    extraction: SkillExtractionResult,
    validations: list[ValidationResult],
) -> DataQualityReport:
    """Assemble the report from the outputs of each pipeline stage."""
    stats = cleaning.stats
    reasons = {
        reason: int(stats.get(f"rejected_{reason}", 0))
        for reason in (
            RejectionReason.MISSING_POSTING_ID,
            RejectionReason.MISSING_TITLE,
            RejectionReason.EXACT_DUPLICATE,
            RejectionReason.CONFLICTING_DUPLICATE_ID,
        )
    }
    title_mapping = (
        jobs.groupby(["title", "role_family"], dropna=False)
        .size()
        .reset_index(name="postings")
        .sort_values(["role_family", "postings"], ascending=[True, False])
    )
    return DataQualityReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source={
            "location": ingestion.source_location,
            **ingestion.metadata,
        },
        row_counts={
            "raw_rows": ingestion.record_count,
            "cleaned_rows": len(jobs),
            "rejected_rows": len(cleaning.rejected),
            "distinct_titles": int(jobs["title"].nunique()),
            "distinct_role_families": int(jobs["role_family"].nunique()),
        },
        rejection_reasons=reasons,
        duplicates={
            "exact_duplicate_rows": reasons[RejectionReason.EXACT_DUPLICATE],
            "duplicate_posting_ids_conflicting": reasons[RejectionReason.CONFLICTING_DUPLICATE_ID],
            "duplicate_skills_within_posting": extraction.stats["skill_tokens_duplicate_in_posting"],
        },
        missing_values_raw=_raw_missing_counts(ingestion.data),
        missing_values_processed={
            column: int(jobs[column].isna().sum()) for column in JOBS_COLUMNS
        },
        salary_quality={
            key.removeprefix("salary_"): value
            for key, value in stats.items()
            if key.startswith("salary_")
        },
        date_quality={
            "posted_on_min": str(jobs["posted_on"].min().date()) if jobs["posted_on"].notna().any() else "n/a",
            "posted_on_max": str(jobs["posted_on"].max().date()) if jobs["posted_on"].notna().any() else "n/a",
            "posted_on_missing": int(stats.get("posted_on_missing", 0)),
            "posted_on_invalid_set_null": int(stats.get("posted_on_invalid_set_null", 0)),
        },
        skills=dict(extraction.stats),
        role_families={
            str(family): int(count)
            for family, count in jobs["role_family"].value_counts().items()
        },
        title_mapping=[
            {"title": str(row.title), "role_family": str(row.role_family), "postings": int(row.postings)}
            for row in title_mapping.itertuples(index=False)
        ],
        validation=[result.to_dict() for result in validations],
    )


def write_quality_report(report: DataQualityReport, output_dir: Path) -> tuple[Path, Path]:
    """Write ``data_quality_report.json`` and ``.md``; returns both paths."""
    json_path = write_json(report.to_dict(), output_dir / f"{REPORT_BASENAME}.json")
    markdown_path = write_text(report.to_markdown(), output_dir / f"{REPORT_BASENAME}.md")
    return json_path, markdown_path
