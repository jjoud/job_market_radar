"""Run only the ingestion stage and log a summary of the raw CSV.

Usage (PowerShell, from the project root):
    python scripts/run_ingestion.py

The raw file is only read; its SHA-256 is checked before and after to prove
it was not modified.
"""

from __future__ import annotations

import sys

from config.settings import ConfigurationError, get_settings
from job_market_radar.ingestion import CsvDataSource, DataSourceError, compute_file_sha256
from job_market_radar.utils.logger import setup_logging


def main() -> int:
    """Entry point. Returns a process exit code."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        setup_logging(level="INFO").error("Configuration error: %s", exc)
        return 2

    settings.ensure_output_directories()
    logger = setup_logging(settings.logs_dir, settings.log_level)

    try:
        checksum_before = (
            compute_file_sha256(settings.raw_data_path)
            if settings.raw_data_path.is_file()
            else None
        )
        result = CsvDataSource(settings.raw_data_path).read()
    except DataSourceError as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1

    if compute_file_sha256(settings.raw_data_path) != checksum_before:
        logger.error("Raw file changed during ingestion: %s", settings.raw_data_path)
        return 1

    empty_cells = (result.data == "").sum()
    logger.info("Columns: %s", ", ".join(result.columns))
    logger.info("Records read: %d", result.record_count)
    for column, count in empty_cells[empty_cells > 0].items():
        logger.info("Empty values in %-16s %d", column + ":", count)
    logger.info("Source SHA-256: %s (unchanged)", result.metadata.get("sha256"))
    logger.info("Ingestion completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
