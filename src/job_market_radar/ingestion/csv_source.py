"""CSV implementation of :class:`DataSource`.

The file is opened read-only and every value is loaded as a string with no
NA conversion, so the returned DataFrame mirrors the file byte-for-byte
(empty cells stay ``""``, whitespace and leading zeros are preserved).
Type conversion and cleaning belong to the transformation stage.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pandas as pd

from job_market_radar.ingestion.base import DataSource, DataSourceError
from job_market_radar.utils.logger import get_logger

logger = get_logger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024


def compute_file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file (used to prove the raw file is unchanged)."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CsvDataSource(DataSource):
    """Read raw job postings from a delimited text file."""

    source_name = "csv"

    def __init__(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        encoding: str = "utf-8-sig",
    ) -> None:
        """
        Args:
            path: CSV file to read.
            delimiter: Field separator.
            encoding: File encoding. ``utf-8-sig`` also strips an optional BOM.
        """
        self.path = Path(path)
        self.delimiter = delimiter
        self.encoding = encoding
        self._checksum: str | None = None

    @property
    def location(self) -> str:
        return str(self.path)

    def _validate_path(self) -> None:
        if not self.path.exists():
            raise DataSourceError(f"CSV file not found: {self.path}")
        if not self.path.is_file():
            raise DataSourceError(f"CSV path is not a file: {self.path}")
        if self.path.stat().st_size == 0:
            raise DataSourceError(f"CSV file is empty: {self.path}")

    def _extract(self) -> pd.DataFrame:
        self._validate_path()
        try:
            data = pd.read_csv(
                self.path,
                sep=self.delimiter,
                encoding=self.encoding,
                dtype=str,
                keep_default_na=False,
                na_filter=False,
                quoting=csv.QUOTE_MINIMAL,
            )
        except UnicodeDecodeError as exc:
            raise DataSourceError(
                f"Cannot decode {self.path} with encoding {self.encoding!r}: {exc}"
            ) from exc
        except pd.errors.EmptyDataError as exc:
            raise DataSourceError(f"CSV file has no header or data: {self.path}") from exc
        except pd.errors.ParserError as exc:
            raise DataSourceError(f"Malformed CSV {self.path}: {exc}") from exc
        except OSError as exc:  # permissions, file locked by Excel, etc.
            raise DataSourceError(f"Cannot open CSV {self.path}: {exc}") from exc

        self._checksum = compute_file_sha256(self.path)
        logger.debug("SHA-256 of %s: %s", self.path.name, self._checksum)
        return data

    def _metadata(self) -> dict[str, str]:
        metadata = {
            "file_name": self.path.name,
            "file_size_bytes": str(self.path.stat().st_size),
            "encoding": self.encoding,
            "delimiter": self.delimiter,
        }
        if self._checksum is not None:
            metadata["sha256"] = self._checksum
        return metadata
