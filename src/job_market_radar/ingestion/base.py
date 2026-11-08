"""Abstract data-source interface.

Every source (CSV today, an HTTP API later) implements :meth:`DataSource._extract`
and returns a raw :class:`pandas.DataFrame`. The shared :meth:`DataSource.read`
wraps it with logging, type checks and lineage metadata, so the rest of the
pipeline only ever depends on :class:`IngestionResult`.

Ingestion never cleans or modifies data: values are returned exactly as the
source delivered them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from job_market_radar.utils.logger import get_logger

logger = get_logger(__name__)


class DataSourceError(Exception):
    """Raised when a data source cannot be read."""


@dataclass(frozen=True)
class IngestionResult:
    """Raw data plus lineage metadata produced by a :class:`DataSource`."""

    data: pd.DataFrame
    source_name: str
    source_location: str
    record_count: int
    columns: tuple[str, ...]
    ingested_at: datetime
    metadata: dict[str, str] = field(default_factory=dict)


class DataSource(ABC):
    """Base class for all raw-data sources."""

    #: Short identifier used in logs and lineage (e.g. ``"csv"``, ``"api"``).
    source_name: str = "unknown"

    @property
    @abstractmethod
    def location(self) -> str:
        """Human-readable origin of the data (file path, URL, ...)."""

    @abstractmethod
    def _extract(self) -> pd.DataFrame:
        """Fetch raw records. Implementations raise :class:`DataSourceError` on failure."""

    def _metadata(self) -> dict[str, str]:
        """Optional source-specific lineage metadata (checksum, API version, ...)."""
        return {}

    def read(self) -> IngestionResult:
        """Extract raw records and wrap them in an :class:`IngestionResult`.

        Raises:
            DataSourceError: If extraction fails or returns something other than a DataFrame.
        """
        logger.info("Reading raw data from %s source: %s", self.source_name, self.location)
        data = self._extract()
        if not isinstance(data, pd.DataFrame):
            raise DataSourceError(
                f"{type(self).__name__}._extract() must return a pandas DataFrame, "
                f"got {type(data).__name__}"
            )

        result = IngestionResult(
            data=data,
            source_name=self.source_name,
            source_location=self.location,
            record_count=len(data),
            columns=tuple(str(column) for column in data.columns),
            ingested_at=datetime.now(timezone.utc),
            metadata=self._metadata(),
        )

        if result.record_count == 0:
            logger.warning("Source %s returned 0 records", self.location)
        logger.info(
            "Read %d records with %d columns from %s",
            result.record_count,
            len(result.columns),
            self.location,
        )
        return result
