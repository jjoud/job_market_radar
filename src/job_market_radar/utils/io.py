"""Safe file writers for processed outputs.

Files are written to a temporary sibling first and then atomically renamed,
so a failed run never leaves a half-written output, and re-running simply
replaces the previous result (idempotent).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

#: UTF-8 with BOM so Excel and Power BI both detect the encoding (e.g. "São Paulo").
CSV_ENCODING = "utf-8-sig"


def _atomic_replace(tmp_path: Path, final_path: Path) -> None:
    try:
        os.replace(tmp_path, final_path)
    except PermissionError as exc:
        tmp_path.unlink(missing_ok=True)
        raise PermissionError(
            f"Cannot overwrite {final_path}. Close it in Excel / Power BI and run again."
        ) from exc


def write_csv(frame: pd.DataFrame, path: Path) -> Path:
    """Write a DataFrame to CSV (ISO dates, NULL as empty cell)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    frame.to_csv(
        tmp_path,
        index=False,
        encoding=CSV_ENCODING,
        date_format="%Y-%m-%d",
        lineterminator="\n",
    )
    _atomic_replace(tmp_path, path)
    return path


def write_text(text: str, path: Path) -> Path:
    """Write UTF-8 text atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8", newline="\n")
    _atomic_replace(tmp_path, path)
    return path


def write_json(data: object, path: Path) -> Path:
    """Write pretty-printed UTF-8 JSON atomically."""
    return write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", path)
