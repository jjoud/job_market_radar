"""Tests for config.settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import PROJECT_ROOT, ConfigurationError, load_settings


def test_defaults_resolve_paths_under_project_root(clean_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    assert settings.project_root == PROJECT_ROOT
    assert settings.raw_data_path == PROJECT_ROOT / "data" / "raw" / "data-job-postings.csv"
    assert settings.staging_data_dir == PROJECT_ROOT / "data" / "staging"
    assert settings.processed_data_dir == PROJECT_ROOT / "data" / "processed"
    assert settings.raw_data_path.is_absolute()
    assert settings.log_level == "INFO"
    assert settings.database.password is None


def test_env_file_is_loaded_but_process_env_wins(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=debug\nPOSTGRES_PORT=6543\n", encoding="utf-8")
    clean_env.setenv("POSTGRES_PORT", "5433")

    settings = load_settings(env_file=env_file, project_root=tmp_path)

    assert settings.log_level == "DEBUG"
    assert settings.database.port == 5433
    assert settings.raw_data_dir == tmp_path / "data" / "raw"


def test_database_url_escapes_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")
    url = load_settings(env_file=None).database.url

    assert url.password == "p@ss:w/rd"
    assert "p@ss:w/rd" not in url.render_as_string(hide_password=True)
    assert url.drivername == "postgresql+psycopg2"


def test_database_url_requires_password(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigurationError, match="POSTGRES_PASSWORD"):
        _ = load_settings(env_file=None).database.url


def test_repr_hides_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_PASSWORD", "super-secret")
    assert "super-secret" not in repr(load_settings(env_file=None))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("POSTGRES_PORT", "abc"),
        ("POSTGRES_PORT", "70000"),
        ("LOG_LEVEL", "verbose"),
        ("RAW_DATA_FILENAME", "../outside.csv"),
    ],
)
def test_invalid_values_raise(clean_env: pytest.MonkeyPatch, name: str, value: str) -> None:
    clean_env.setenv(name, value)
    with pytest.raises(ConfigurationError):
        load_settings(env_file=None)


def test_ensure_output_directories_does_not_create_raw(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = load_settings(env_file=None, project_root=tmp_path)
    settings.ensure_output_directories()

    assert settings.staging_data_dir.is_dir()
    assert settings.processed_data_dir.is_dir()
    assert settings.logs_dir.is_dir()
    assert not settings.raw_data_dir.exists()
