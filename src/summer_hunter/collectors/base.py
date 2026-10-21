"""Common collector interface."""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from summer_hunter.engine.normalization import TitleRules
from summer_hunter.models.opportunity import CollectorResult
from summer_hunter.utils.http import PoliteClient

log = logging.getLogger(__name__)


@dataclass
class CollectContext:
    http: PoliteClient
    title_rules: TitleRules
    # (source, source_id, updated_marker) -> True when the stored record is unchanged and fresh
    is_unchanged: Callable[[str, str, str | None], bool] = lambda *_: False
    max_detail_fetches: int = 60
    dry_run: bool = False
    extra: dict = field(default_factory=dict)


class Collector(abc.ABC):
    """A source of opportunities. Implementations must never raise for expected source failures;
    they return a CollectorResult with status 'failed'/'partial' and readable errors instead."""

    name: str = "collector"

    @abc.abstractmethod
    async def collect(self, ctx: CollectContext) -> CollectorResult:
        ...

    def result(self, **kwargs) -> CollectorResult:
        return CollectorResult(source=self.name, **kwargs)


def parse_dt(value: str | int | float | None) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            ts = float(value)
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return None
