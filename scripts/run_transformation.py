"""Run ingestion -> source validation -> transformation -> transformed validation.

Writes (raw data is only read, never written):
    data/processed/jobs.csv
    data/processed/skills.csv
    data/processed/job_skills.csv
    data/processed/rejected_records.csv
    reports/data_quality_report.json
    reports/data_quality_report.md

Usage (PowerShell, from the project root):
    python scripts/run_transformation.py

Exit codes: 0 success, 1 data/validation failure, 2 configuration error.
"""

from __future__ import annotations

import sys

from config.settings import ConfigurationError, get_settings
from job_market_radar.ingestion import CsvDataSource, DataSourceError, compute_file_sha256
from job_market_radar.transformation import (
    build_jobs_table,
    clean_job_postings,
    extract_skills,
    normalize_job_postings,
)
from job_market_radar.transformation.cleaning import normalize_whitespace
from job_market_radar.utils.io import write_csv
from job_market_radar.utils.logger import setup_logging
from job_market_radar.validation import (
    build_quality_report,
    validate_source,
    validate_transformed,
    write_quality_report,
)


def main() -> int:
    """Entry point. Returns a process exit code."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        setup_logging(level="INFO").error("Configuration error: %s", exc)
        return 2

    settings.ensure_output_directories()
    logger = setup_logging(settings.logs_dir, settings.log_level)
    raw_path = settings.raw_data_path

    try:
        ingestion = CsvDataSource(raw_path).read()
    except DataSourceError as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1
    raw_checksum = ingestion.metadata["sha256"]

    source_validation = validate_source(ingestion.data)
    source_validation.log_summary(logger)
    if not source_validation.passed:
        logger.error("Stopping: source data failed validation")
        return 1

    cleaning = clean_job_postings(ingestion.data)
    normalized = normalize_job_postings(cleaning.data)
    jobs = build_jobs_table(normalized)
    extraction = extract_skills(normalized)

    expected_titles = set(normalize_whitespace(ingestion.data["title"]).dropna()) - {""}
    transformed_validation = validate_transformed(
        jobs, extraction.skills, extraction.job_skills, expected_titles=expected_titles
    )
    transformed_validation.log_summary(logger)

    report = build_quality_report(
        ingestion=ingestion,
        cleaning=cleaning,
        jobs=jobs,
        extraction=extraction,
        validations=[source_validation, transformed_validation],
    )
    try:
        json_path, markdown_path = write_quality_report(report, settings.reports_dir)
        write_csv(cleaning.rejected, settings.processed_data_dir / "rejected_records.csv")
        logger.info("Quality report written: %s | %s", json_path, markdown_path)

        if not transformed_validation.passed:
            logger.error("Stopping: transformed data failed validation; processed tables not written")
            return 1

        for name, frame in (
            ("jobs", jobs),
            ("skills", extraction.skills),
            ("job_skills", extraction.job_skills),
        ):
            path = write_csv(frame, settings.processed_data_dir / f"{name}.csv")
            logger.info("Wrote %-10s %6d rows -> %s", name, len(frame), path)
    except PermissionError as exc:
        logger.error("%s", exc)
        return 1

    if compute_file_sha256(raw_path) != raw_checksum:
        logger.error("Raw file changed during the run: %s", raw_path)
        return 1

    logger.info(
        "Done: %d raw rows -> %d jobs (%d rejected), %d unique skills, %d job-skill pairs. "
        "Raw file unchanged.",
        ingestion.record_count,
        len(jobs),
        len(cleaning.rejected),
        len(extraction.skills),
        len(extraction.job_skills),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
