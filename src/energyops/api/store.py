"""Where finished run records are kept while the API is running.

This is memory only: records are lost when the server stops, and the oldest are dropped once
the limit is reached. A database comes in a later phase.
"""

from __future__ import annotations

from collections import OrderedDict

from energyops.agents.runs import RunRecord


class RunStore:
    def __init__(self, limit: int = 200) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._limit = limit
        self._records: OrderedDict[str, RunRecord] = OrderedDict()

    def add(self, record: RunRecord) -> None:
        self._records[record.run_id] = record
        while len(self._records) > self._limit:
            self._records.popitem(last=False)

    def get(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def newest_first(self, agent: str | None = None, limit: int = 50) -> list[RunRecord]:
        records = reversed(self._records.values())
        return [r for r in records if agent is None or r.agent == agent][:limit]
