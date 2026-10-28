import math

from energyops.ingest import MAX_ISSUES_SHOWN, MAX_ROWS, gap_count, parse_csv


def csv(*lines: str, header: str = "timestamp,power_kw") -> bytes:
    return ("\n".join([header, *lines]) + "\n").encode()


def test_a_good_file_is_read_and_converted_to_utc():
    report = parse_csv(
        csv("2026-03-01T01:00:00+01:00,10.5", "2026-03-01T00:15:00Z,12"), interval_minutes=15
    )
    assert report.ok
    assert [r.timestamp.isoformat() for r in report.readings] == [
        "2026-03-01T00:00:00+00:00",
        "2026-03-01T00:15:00+00:00",
    ]
    assert [r.power_kw for r in report.readings] == [10.5, 12.0]
    assert report.first and report.last and report.first < report.last


def test_blank_lines_and_a_byte_order_mark_are_harmless():
    data = b"\xef\xbb\xbftimestamp,power_kw\n\n2026-03-01T00:00:00Z,1\n\n"
    assert parse_csv(data, interval_minutes=15).ok


def test_a_timestamp_without_an_offset_is_refused_not_guessed():
    report = parse_csv(csv("2026-03-01T00:00:00,5"), interval_minutes=15)
    assert not report.ok
    assert "offset" in report.issues[0].message
    assert report.readings == []


def test_a_timestamp_off_the_interval_boundary_is_refused():
    report = parse_csv(csv("2026-03-01T00:10:00Z,5"), interval_minutes=15)
    assert "15 minute boundary" in report.issues[0].message


def test_duplicates_point_at_the_first_line():
    report = parse_csv(
        csv("2026-03-01T00:00:00Z,5", "2026-03-01T01:00:00+01:00,6"), interval_minutes=15
    )
    assert report.issues[0].line == 3
    assert "line 2" in report.issues[0].message


def test_bad_power_values_are_refused():
    for bad in ["-1", "nan", "inf", "1_000", "abc", "", "2000000", "1e400"]:
        report = parse_csv(csv(f"2026-03-01T00:00:00Z,{bad}"), interval_minutes=15)
        assert not report.ok, bad
        assert report.readings == []


def test_zero_and_scientific_notation_are_fine():
    report = parse_csv(
        csv("2026-03-01T00:00:00Z,0", "2026-03-01T00:15:00Z,1.5e2"), interval_minutes=15
    )
    assert report.ok
    assert report.readings[1].power_kw == 150.0
    assert all(math.isfinite(r.power_kw) for r in report.readings)


def test_wrong_header_and_wrong_column_count():
    assert not parse_csv(csv("2026-03-01T00:00:00Z,1", header="time,kw"), interval_minutes=15).ok
    report = parse_csv(csv("2026-03-01T00:00:00Z,1,9"), interval_minutes=15)
    assert "2 columns" in report.issues[0].message


def test_empty_and_header_only_files():
    assert not parse_csv(b"", interval_minutes=15).ok
    report = parse_csv(b"timestamp,power_kw\n", interval_minutes=15)
    assert "no readings" in report.issues[0].message


def test_non_utf8_and_binary_files_are_refused():
    assert not parse_csv(b"\xff\xfe\x00\x00", interval_minutes=15).ok
    assert (
        "binary"
        in parse_csv(b"timestamp,power_kw\n\x00\x01", interval_minutes=15).issues[0].message
    )


def test_one_bad_row_rejects_the_whole_file():
    report = parse_csv(
        csv("2026-03-01T00:00:00Z,1", "2026-03-01T00:15:00Z,oops"), interval_minutes=15
    )
    assert not report.ok and report.readings == []
    assert report.issues[0].line == 3


def test_only_the_first_issues_are_listed_but_all_are_counted():
    lines = [f"2026-03-01T00:00:00Z,-{i}" for i in range(1, 60)]
    report = parse_csv(csv(*lines), interval_minutes=15)
    assert len(report.issues) == MAX_ISSUES_SHOWN
    assert report.issue_count >= 59


def test_too_many_rows_and_too_many_bytes():
    rows = [f"2026-03-01T00:00:00Z,{i}" for i in range(MAX_ROWS + 1)]
    report = parse_csv(csv(*rows), interval_minutes=15)
    assert not report.ok and report.readings == []
    big = b"timestamp,power_kw\n" + b"#" * (5 * 1024 * 1024 + 1)
    assert not parse_csv(big, interval_minutes=15).ok


def test_gaps_are_counted():
    report = parse_csv(csv("2026-03-01T00:00:00Z,1", "2026-03-01T01:00:00Z,1"), interval_minutes=15)
    assert gap_count(report, 15) == 3
