"""Normalisation of titles and categorical columns.

* ``title_raw`` (exact source value) is never touched.
* ``title`` is the whitespace-cleaned title; all distinct titles are kept.
* ``role_family`` is the title without its level prefix, mapped to a canonical
  name. Titles that are not in the alias table keep their own wording, so no
  job category is ever collapsed into "Other".
* Categorical columns are lower-cased and mapped through small alias tables.
  Unknown values are kept (lower-cased) and flagged by validation, not dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Mapping

import pandas as pd

from job_market_radar.schema import COMPANY_SIZES, JOBS_COLUMNS, SENIORITY_LEVELS, WORK_ARRANGEMENTS

_KEY_SEPARATORS: Final = re.compile(r"[\s\-_.]+")

# Level words that can prefix a title, mapped to canonical seniority.
TITLE_LEVEL_PREFIXES: Final[Mapping[str, str]] = {
    "junior": "junior",
    "jr": "junior",
    "senior": "senior",
    "sr": "senior",
    "staff": "staff",
    "lead": "lead",
}

# Normalised title remainder -> canonical role family.
ROLE_FAMILY_ALIASES: Final[Mapping[str, str]] = {
    "data analyst": "Data Analyst",
    "data engineer": "Data Engineer",
    "data scientist": "Data Scientist",
    "analytics engineer": "Analytics Engineer",
    "bi developer": "BI Developer",
    "business intelligence developer": "BI Developer",
    "machine learning engineer": "Machine Learning Engineer",
    "ml engineer": "Machine Learning Engineer",
    "data platform engineer": "Data Platform Engineer",
    "research scientist": "Research Scientist",
}

SENIORITY_ALIASES: Final[Mapping[str, str]] = {
    **{level: level for level in SENIORITY_LEVELS},
    "jr": "junior",
    "entry level": "junior",
    "sr": "senior",
    "mid level": "mid",
    "intermediate": "mid",
}

WORK_ARRANGEMENT_ALIASES: Final[Mapping[str, str]] = {
    **{value: value for value in WORK_ARRANGEMENTS},
    "on site": "onsite",
    "in office": "onsite",
    "remote first": "remote",
}

COMPANY_SIZE_ALIASES: Final[Mapping[str, str]] = {
    **{value: value for value in COMPANY_SIZES},
    "start up": "startup",
    "scale up": "scaleup",
    "mid market": "midmarket",
}


def normalize_key(value: str) -> str:
    """Lower-case and turn runs of whitespace, ``-``, ``_`` and ``.`` into one space."""
    return _KEY_SEPARATORS.sub(" ", value.lower()).strip()


@dataclass(frozen=True)
class ParsedTitle:
    """Result of splitting a title into level prefix and role family."""

    level: str | None
    role_family: str
    is_mapped: bool


@lru_cache(maxsize=1024)
def parse_title(title: str) -> ParsedTitle:
    """Split a cleaned title into its level prefix and canonical role family.

    Examples:
        ``"Senior Data Engineer"`` -> level ``senior``, family ``Data Engineer``.
        ``"Sr. BI Developer"``     -> level ``senior``, family ``BI Developer``.
        ``"Data Steward"``         -> no level, family ``Data Steward`` (unmapped, kept).
    """
    key = normalize_key(title)
    first_word, _, remainder_key = key.partition(" ")
    level = TITLE_LEVEL_PREFIXES.get(first_word) if remainder_key else None
    if level is None:
        remainder_key = key

    family = ROLE_FAMILY_ALIASES.get(remainder_key)
    if family is not None:
        return ParsedTitle(level=level, role_family=family, is_mapped=True)

    original_remainder = title.split(None, 1)[1] if level else title
    return ParsedTitle(level=level, role_family=original_remainder.strip(), is_mapped=False)


def normalize_category(series: pd.Series, aliases: Mapping[str, str]) -> pd.Series:
    """Map values through ``aliases`` after key normalisation; unknown values are kept lower-cased."""
    keys = series.astype("string").map(normalize_key, na_action="ignore")
    return keys.map(lambda key: aliases.get(key, key), na_action="ignore").astype("string")


def normalize_job_postings(data: pd.DataFrame) -> pd.DataFrame:
    """Add ``role_family`` and normalise categorical columns. Returns a new DataFrame."""
    out = data.copy()
    parsed = out["title"].map(parse_title, na_action="ignore")
    out["role_family"] = parsed.map(lambda item: item.role_family, na_action="ignore").astype(
        "string"
    )
    out["seniority"] = normalize_category(out["seniority"], SENIORITY_ALIASES)
    out["work_arrangement"] = normalize_category(out["remote"], WORK_ARRANGEMENT_ALIASES)
    out["company_size"] = normalize_category(out["company_size"], COMPANY_SIZE_ALIASES)
    out["country"] = out["country"].astype("string").str.upper()
    return out.drop(columns=["remote"])


def build_jobs_table(normalized: pd.DataFrame) -> pd.DataFrame:
    """Select the processed ``jobs`` columns in their canonical order."""
    return normalized.loc[:, list(JOBS_COLUMNS)].reset_index(drop=True)
