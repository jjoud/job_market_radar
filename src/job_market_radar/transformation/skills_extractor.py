"""Split the ``skills`` column into a skills dimension and a job-skill bridge.

The source uses ``|`` as its only delimiter (verified on the real file).
Each token is trimmed, lower-cased and resolved through an alias table, so
``"Power BI"``, ``"power  bi"``, ``"Power-BI"`` and ``"powerbi"`` all become
the single key ``power bi``. A skill repeated inside one posting is counted once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Mapping

import pandas as pd

from job_market_radar.schema import (
    JOB_SKILLS_COLUMNS,
    KEY_COLUMN,
    SKILLS_COLUMNS,
    SKILLS_DELIMITER,
)
from job_market_radar.utils.logger import get_logger

logger = get_logger(__name__)

# Canonical key -> display name. Covers every skill in the dataset plus alias targets.
SKILL_DISPLAY_NAMES: Final[Mapping[str, str]] = {
    "airflow": "Airflow",
    "aws": "AWS",
    "dax": "DAX",
    "dbt": "dbt",
    "excel": "Excel",
    "experimentation": "Experimentation",
    "git": "Git",
    "jax": "JAX",
    "kafka": "Kafka",
    "kubernetes": "Kubernetes",
    "looker": "Looker",
    "mlflow": "MLflow",
    "modelling": "Modelling",
    "observability": "Observability",
    "pandas": "pandas",
    "postgresql": "PostgreSQL",
    "power bi": "Power BI",
    "python": "Python",
    "pytorch": "PyTorch",
    "research": "Research",
    "snowflake": "Snowflake",
    "spark": "Spark",
    "sql": "SQL",
    "statistics": "Statistics",
    "tableau": "Tableau",
    "terraform": "Terraform",
}

# Alternative spelling (already lower-cased, single-spaced) -> canonical key.
SKILL_ALIASES: Final[Mapping[str, str]] = {
    "postgres": "postgresql",
    "microsoft power bi": "power bi",
    "ms excel": "excel",
    "microsoft excel": "excel",
    "k8s": "kubernetes",
    "amazon web services": "aws",
    "apache airflow": "airflow",
    "apache spark": "spark",
    "apache kafka": "kafka",
    "modeling": "modelling",
}

_WHITESPACE_RUN: Final = re.compile(r"\s+")
_COMPACT_STRIP: Final = re.compile(r"[\s\-_.]")


def _basic_key(token: str) -> str:
    return _WHITESPACE_RUN.sub(" ", token.strip().lower())


def _compact(key: str) -> str:
    return _COMPACT_STRIP.sub("", key)


# "powerbi", "power-bi", "power_bi" ... -> "power bi"
_COMPACT_INDEX: Final[Mapping[str, str]] = {
    _compact(spelling): canonical
    for spelling, canonical in {
        **{key: key for key in SKILL_DISPLAY_NAMES},
        **SKILL_ALIASES,
    }.items()
}


def normalize_skill_key(token: object) -> str | None:
    """Return the canonical key for a raw skill token, or ``None`` if it is blank."""
    if token is None or token is pd.NA or (isinstance(token, float) and pd.isna(token)):
        return None
    basic = _basic_key(str(token))
    if not basic:
        return None
    if basic in SKILL_ALIASES:
        return SKILL_ALIASES[basic]
    if basic in SKILL_DISPLAY_NAMES:
        return basic
    return _COMPACT_INDEX.get(_compact(basic), basic)


def skill_display_name(key: str) -> str:
    """Human-friendly name; unknown skills keep their normalised (lower-case) key."""
    return SKILL_DISPLAY_NAMES.get(key, key)


@dataclass(frozen=True)
class SkillExtractionResult:
    """Skills dimension, job-skill bridge and extraction counters."""

    skills: pd.DataFrame
    job_skills: pd.DataFrame
    stats: dict[str, int] = field(default_factory=dict)


def extract_skills(
    postings: pd.DataFrame,
    *,
    delimiter: str = SKILLS_DELIMITER,
) -> SkillExtractionResult:
    """Explode the ``skills`` column into normalised skill records.

    Args:
        postings: Cleaned postings with ``posting_id`` and ``skills`` columns.
        delimiter: Separator used inside ``skills``.
    """
    tokens = (
        postings.loc[:, [KEY_COLUMN, "skills"]]
        .assign(token=lambda frame: frame["skills"].astype("string").str.split(delimiter, regex=False))
        .explode("token")
    )
    tokens["skill_key"] = tokens["token"].map(normalize_skill_key)

    present_tokens = tokens["token"].notna()
    keyed = tokens.loc[tokens["skill_key"].notna()]
    duplicate_in_posting = keyed.duplicated(subset=[KEY_COLUMN, "skill_key"], keep="first")
    resolved_by_alias = keyed["token"].map(_basic_key) != keyed["skill_key"]

    job_skills = (
        keyed.loc[~duplicate_in_posting, list(JOB_SKILLS_COLUMNS)]
        .astype("string")
        .reset_index(drop=True)
    )
    skill_keys = sorted(job_skills["skill_key"].unique())
    skills = pd.DataFrame(
        {
            "skill_key": pd.array(skill_keys, dtype="string"),
            "skill_name": pd.array([skill_display_name(key) for key in skill_keys], dtype="string"),
        },
        columns=list(SKILLS_COLUMNS),
    )

    postings_with_skills = set(job_skills[KEY_COLUMN])
    stats = {
        "skill_tokens_total": int(present_tokens.sum()),
        "skill_tokens_empty": int((present_tokens & tokens["skill_key"].isna()).sum()),
        "skill_tokens_duplicate_in_posting": int(duplicate_in_posting.sum()),
        "skill_tokens_resolved_by_alias": int(resolved_by_alias.sum()),
        "job_skill_pairs": len(job_skills),
        "unique_skills": len(skills),
        "postings_without_skills": int((~postings[KEY_COLUMN].isin(postings_with_skills)).sum()),
    }
    logger.info(
        "Skills: %d tokens -> %d job-skill pairs, %d unique skills",
        stats["skill_tokens_total"],
        stats["job_skill_pairs"],
        stats["unique_skills"],
    )
    return SkillExtractionResult(skills=skills, job_skills=job_skills, stats=stats)
