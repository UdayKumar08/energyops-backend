"""Reading meter data from a CSV file.

The file has one header line, `timestamp,power_kw`, and one line per interval. A timestamp is an
ISO 8601 date and time that carries its UTC offset (`2026-03-01T00:15:00+00:00` or `...Z`); it is
stored in UTC. A file without offsets is refused rather than guessed at, because a wrong guess
would shift every reading and nothing would flag it. `power_kw` is average power over the
interval that starts at the timestamp, in kW. It is never energy.

The file is accepted whole or not at all. When it is refused the answer lists the first problems
with their line numbers, so the person can fix the file and send it again. Nothing here touches
a database: the same code runs for a dry run and for the real upload.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 200_000
MAX_ISSUES_SHOWN = 20
HEADER = ("timestamp", "power_kw")
# Matches the database's own limit on a reading.
MAX_POWER_KW = 1_000_000.0

# Plain decimal numbers only. Python's float() also accepts "1_000", "nan" and "infinity".
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


@dataclass(frozen=True, slots=True)
class ParsedReading:
    timestamp: datetime  # UTC
    power_kw: float


@dataclass(frozen=True, slots=True)
class Issue:
    line: int  # 1 is the header line
    message: str


@dataclass(slots=True)
class IngestReport:
    readings: list[ParsedReading] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    issue_count: int = 0
    rows: int = 0

    @property
    def ok(self) -> bool:
        return self.issue_count == 0 and bool(self.readings)

    @property
    def first(self) -> datetime | None:
        return min((r.timestamp for r in self.readings), default=None)

    @property
    def last(self) -> datetime | None:
        return max((r.timestamp for r in self.readings), default=None)

    def add_issue(self, line: int, message: str) -> None:
        self.issue_count += 1
        if len(self.issues) < MAX_ISSUES_SHOWN:
            self.issues.append(Issue(line, message))


def _parse_timestamp(text: str, interval_minutes: int) -> datetime | str:
    """A UTC datetime, or the reason the text is not acceptable."""
    try:
        value = datetime.fromisoformat(text.strip())
    except ValueError:
        return "the timestamp is not an ISO 8601 date and time"
    if value.tzinfo is None or value.utcoffset() is None:
        return "the timestamp has no UTC offset (write +00:00 or Z)"
    utc = value.astimezone(UTC)
    minute = utc.hour * 60 + utc.minute
    if utc.second or utc.microsecond or minute % interval_minutes:
        return f"the timestamp is not on a {interval_minutes} minute boundary"
    return utc


def _parse_power(text: str) -> float | str:
    cleaned = text.strip()
    if not _NUMBER.match(cleaned):
        return "power_kw is not a plain number"
    value = float(cleaned)
    if not math.isfinite(value):
        return "power_kw is not a finite number"
    if value < 0:
        return "power_kw is negative"
    if value > MAX_POWER_KW:
        return f"power_kw is above {MAX_POWER_KW:,.0f}"
    return value


def parse_csv(data: bytes, *, interval_minutes: int) -> IngestReport:
    """Check a CSV file and return what it holds. It never raises for a bad file; the report's
    issues say what is wrong."""
    report = IngestReport()
    if interval_minutes <= 0 or 1440 % interval_minutes:
        raise ValueError("interval_minutes must divide 1440 evenly")
    if len(data) > MAX_BYTES:
        report.add_issue(0, f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")
        return report
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        report.add_issue(0, "the file is not UTF-8 text")
        return report
    if "\x00" in text:
        report.add_issue(0, "the file contains binary data")
        return report

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        report.add_issue(1, "the file is empty")
        return report
    except csv.Error as exc:
        report.add_issue(1, f"the file cannot be read as CSV ({exc})")
        return report
    if tuple(cell.strip().lower() for cell in header) != HEADER:
        report.add_issue(1, "the header line must be exactly: timestamp,power_kw")
        return report

    seen: dict[datetime, int] = {}
    try:
        for row in reader:
            line = reader.line_num
            if not row or all(not cell.strip() for cell in row):
                continue  # a blank line is harmless
            report.rows += 1
            if report.rows > MAX_ROWS:
                report.add_issue(line, f"the file has more than {MAX_ROWS:,} rows")
                report.readings.clear()
                return report
            if len(row) != 2:
                report.add_issue(line, f"expected 2 columns, found {len(row)}")
                continue
            stamp = _parse_timestamp(row[0], interval_minutes)
            power = _parse_power(row[1])
            if isinstance(stamp, str):
                report.add_issue(line, stamp)
            if isinstance(power, str):
                report.add_issue(line, power)
            if isinstance(stamp, str) or isinstance(power, str):
                continue
            if stamp in seen:
                report.add_issue(line, f"this timestamp already appears on line {seen[stamp]}")
                continue
            seen[stamp] = line
            report.readings.append(ParsedReading(stamp, power))
    except csv.Error as exc:
        report.add_issue(reader.line_num, f"the file cannot be read as CSV ({exc})")

    if not report.readings and report.issue_count == 0:
        report.add_issue(1, "the file has a header but no readings")
    if report.issue_count:
        report.readings.clear()  # all or nothing
    else:
        report.readings.sort(key=lambda r: r.timestamp)
    return report


def gap_count(report: IngestReport, interval_minutes: int) -> int:
    """How many intervals are missing between the first and last reading. Missing intervals are
    allowed (meters drop out), but the person is told about them."""
    if len(report.readings) < 2:
        return 0
    first, last = report.readings[0].timestamp, report.readings[-1].timestamp
    expected = int((last - first) / timedelta(minutes=interval_minutes)) + 1
    return max(0, expected - len(report.readings))
