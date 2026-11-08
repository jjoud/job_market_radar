"""Centralised project configuration.

All file-system paths are derived from the project root with :mod:`pathlib`,
and every environment-specific or sensitive value (database credentials,
log level, source file name) is read from environment variables, optionally
loaded from a ``.env`` file. Nothing sensitive is hard-coded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE: Path = PROJECT_ROOT / ".env"

DEFAULT_RAW_DATA_FILENAME = "data-job-postings.csv"
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigurationError(ValueError):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class DatabaseSettings:
    """PostgreSQL connection settings (password is never logged)."""

    host: str
    port: int
    name: str
    user: str
    password: str | None

    @property
    def url(self) -> URL:
        """Return a SQLAlchemy URL; special characters in the password are escaped safely."""
        missing = [
            env_name
            for env_name, value in (
                ("POSTGRES_DB", self.name),
                ("POSTGRES_USER", self.user),
                ("POSTGRES_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"Missing database settings: {', '.join(missing)}. "
                "Set them in your .env file (see .env.example)."
            )
        return URL.create(
            drivername="postgresql+psycopg2",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.name,
        )

    def __repr__(self) -> str:  # keep the password out of logs and tracebacks
        return (
            f"DatabaseSettings(host={self.host!r}, port={self.port}, "
            f"name={self.name!r}, user={self.user!r}, password='***')"
        )


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    project_root: Path
    raw_data_dir: Path
    staging_data_dir: Path
    processed_data_dir: Path
    logs_dir: Path
    reports_dir: Path
    sql_dir: Path
    raw_data_filename: str
    log_level: str
    database: DatabaseSettings

    @property
    def raw_data_path(self) -> Path:
        """Absolute path of the source CSV file."""
        return self.raw_data_dir / self.raw_data_filename

    def ensure_output_directories(self) -> None:
        """Create writable output folders if missing. The raw folder is never touched."""
        for directory in (
            self.staging_data_dir,
            self.processed_data_dir,
            self.logs_dir,
            self.reports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def _parse_port(raw_value: str) -> int:
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"POSTGRES_PORT must be an integer, got {raw_value!r}") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"POSTGRES_PORT out of range: {port}")
    return port


def _parse_log_level(raw_value: str) -> str:
    level = raw_value.strip().upper()
    if level not in VALID_LOG_LEVELS:
        raise ConfigurationError(
            f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}, got {raw_value!r}"
        )
    return level


def load_settings(
    env_file: Path | None = DEFAULT_ENV_FILE,
    project_root: Path = PROJECT_ROOT,
) -> Settings:
    """Build :class:`Settings` from environment variables.

    Args:
        env_file: Optional ``.env`` file to load. Variables already present in
            the process environment take precedence over the file.
        project_root: Base directory for all derived paths (overridable in tests).

    Raises:
        ConfigurationError: If a value is present but invalid.
    """
    if env_file is not None and env_file.is_file():
        load_dotenv(env_file, override=False)

    raw_data_filename = os.getenv("RAW_DATA_FILENAME", DEFAULT_RAW_DATA_FILENAME).strip()
    if not raw_data_filename or Path(raw_data_filename).name != raw_data_filename:
        raise ConfigurationError(
            "RAW_DATA_FILENAME must be a plain file name inside data/raw/, "
            f"got {raw_data_filename!r}"
        )

    data_dir = project_root / "data"
    database = DatabaseSettings(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=_parse_port(os.getenv("POSTGRES_PORT", "5432")),
        name=os.getenv("POSTGRES_DB", "job_market_radar"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD") or None,
    )

    return Settings(
        project_root=project_root,
        raw_data_dir=data_dir / "raw",
        staging_data_dir=data_dir / "staging",
        processed_data_dir=data_dir / "processed",
        logs_dir=project_root / "logs",
        reports_dir=project_root / "reports",
        sql_dir=project_root / "sql",
        raw_data_filename=raw_data_filename,
        log_level=_parse_log_level(os.getenv("LOG_LEVEL", "INFO")),
        database=database,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide cached settings loaded from the default ``.env``."""
    return load_settings()
