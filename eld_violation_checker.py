#!/usr/bin/env python3
"""Check ELD duty log rows for US Hours of Service (HOS) violations.

Input is a JSON file holding a list of log entries, or a CSV file
(with a header row) whose columns are driver, date, start, end, and
status — the same fields as the JSON format. A JSON entry is an
object with:

    {"driver": "Jane Doe", "date": "2026-10-26",
     "start": "08:00", "end": "12:00", "status": "DRIVING"}

Dates are YYYY-MM-DD and times are HH:MM (24 hour). Status is one of
OFF, SB (sleeper berth), DRIVING, or ON (on duty, not driving).
Entries for one driver are sorted by start time before checking.

Rules checked (property-carrying, FMCSA Part 395):

- 11-hour driving limit: no more than 11 hours driving after 10
  consecutive hours off duty.
- 14-hour window: no driving after 14 hours have elapsed since the
  driver came on duty after 10 consecutive hours off.
- 30-minute break: no driving after 8 cumulative hours of driving
  without an interruption of at least 30 minutes off duty or in the
  sleeper berth.
- 60/70-hour limit: no more than 60 on-duty hours in 7 consecutive
  days, or 70 in 8 days (choose with --limit, default 70).
- Data problems: overlapping entries, end before start, unknown
  status, and missing fields are reported as errors, not violations.

A run of 10 or more consecutive hours OFF or SB resets the 11-hour,
14-hour, and 30-minute break counters. This tool is a log checker, not
legal advice: it flags what the supplied rows show, nothing more.

Usage:

    python3 eld_violation_checker.py examples/log.json
    python3 eld_violation_checker.py log.json --json
    python3 eld_violation_checker.py log.json --limit 60
    python3 eld_violation_checker.py log.json --check
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta

STATUSES = ("OFF", "SB", "DRIVING", "ON")
ON_DUTY = ("DRIVING", "ON")
RESET_MINUTES = 10 * 60
BREAK_MINUTES = 30


def parse_time(date_text, time_text):
    """Parse a YYYY-MM-DD date plus HH:MM time into a datetime."""
    return datetime.strptime(date_text + " " + time_text, "%Y-%m-%d %H:%M")


def minutes_between(start, end):
    return (end - start).total_seconds() / 60.0


def hours(value_minutes):
    return round(value_minutes / 60.0, 2)


CSV_COLUMNS = ("driver", "date", "start", "end", "status")


def load_csv(path):
    """Load duty log rows from a CSV file with a header row.

    The header must name the same columns as the JSON format (driver,
    date, start, end, status); extra columns are ignored. Values are
    read as text, so no quoting beyond the CSV rules is needed.
    """
    entries = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV file is missing its header row")
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        missing = [name for name in CSV_COLUMNS
                   if name not in reader.fieldnames]
        if missing:
            raise ValueError(
                "CSV header is missing column(s): %s" % ", ".join(missing))
        for row in reader:
            entry = {}
            for name in CSV_COLUMNS:
                value = row.get(name)
                entry[name] = "" if value is None else value.strip()
            entries.append(entry)
    return entries


def load_entries(path):
    """Load log entries from a JSON or CSV file.

    A path ending in .csv is read as CSV with a header row; anything
    else is read as JSON. Both return the same list of entry dicts.
    """
    if path.lower().endswith(".csv"):
        return load_csv(path)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("log file must hold a JSON list of entries")
    return data


def check_driver(driver, entries, limit):
    """Check one driver's entries. Returns (violations, errors, stats)."""
    violations = []
    errors = []
    rows = []
    for index, entry in enumerate(entries):
        if any(field not in entry for field in ("date", "start", "end", "status")):
            for field in ("date", "start", "end", "status"):
                if field not in entry:
                    errors.append(
                        "entry %d: missing field %s" % (index, field))
            continue
        status = str(entry["status"]).upper()
        if status not in STATUSES:
            errors.append(
                "entry %d: unknown status %r" % (index, entry["status"]))
            continue
        try:
            start = parse_time(entry["date"], entry["start"])
            end = parse_time(entry["date"], entry["end"])
        except ValueError:
            errors.append(
                "entry %d: bad date or time %r %r"
                % (index, entry["date"], entry["start"]))
            continue
        if entry["end"] < entry["start"] and end <= start:
            # An end earlier than the start on the same date is an error
            # unless the row spans midnight, which this format does not
            # support; flag it instead of silently rolling over.
            errors.append(
                "entry %d: end %s is before start %s"
                % (index, entry["end"], entry["start"]))
            continue
        rows.append((start, end, status, entry["date"]))
    rows.sort()

    for i in range(1, len(rows)):
        if rows[i][0] < rows[i - 1][1]:
            errors.append(
                "overlap: %s %s overlaps the previous entry"
                % (rows[i][3], rows[i][0].strftime("%H:%M")))

    # Rolling on-duty totals per calendar day, for the 60/70 rule.
    on_duty_by_day = {}
    for start, end, status, date_text in rows:
        if status in ON_DUTY:
            on_duty_by_day[date_text] = (
                on_duty_by_day.get(date_text, 0.0)
                + minutes_between(start, end))

    def on_duty_in_window(day_text, days):
        day = datetime.strptime(day_text, "%Y-%m-%d").date()
        total = 0.0
        for offset in range(days):
            key = (day - timedelta(days=offset)).isoformat()
            total += on_duty_by_day.get(key, 0.0)
        return total

    # Walk the timeline tracking one duty period at a time.
    driving_minutes = 0.0
    period_start = None
    driving_since_break = 0.0
    flagged = {"11h": set(), "14h": set(), "break": set(), "limit": set()}
    total_driving = 0.0
    total_on_duty = 0.0

    for start, end, status, date_text in rows:
        span = minutes_between(start, end)
        if status in ON_DUTY:
            total_on_duty += span
        if status == "DRIVING":
            total_driving += span

        if status in ("OFF", "SB"):
            if span >= RESET_MINUTES:
                driving_minutes = 0.0
                period_start = None
                driving_since_break = 0.0
            elif span >= BREAK_MINUTES:
                driving_since_break = 0.0
            continue

        if status == "ON":
            if period_start is None:
                period_start = start
            continue

        # DRIVING
        if period_start is None:
            period_start = start
        window_hours = minutes_between(period_start, start) / 60.0
        window_end = minutes_between(period_start, end) / 60.0
        if window_end > 14.0 and start not in flagged["14h"]:
            violations.append({
                "driver": driver, "date": date_text,
                "rule": "14-hour window",
                "detail": "driving at %s, %.2f hours after coming on duty"
                          % (start.strftime("%H:%M"), window_hours),
            })
            flagged["14h"].add(start)
        if driving_since_break >= 8 * 60 and start not in flagged["break"]:
            violations.append({
                "driver": driver, "date": date_text,
                "rule": "30-minute break",
                "detail": "driving at %s after %.2f hours driving without "
                          "a 30-minute break"
                          % (start.strftime("%H:%M"),
                             driving_since_break / 60.0),
            })
            flagged["break"].add(start)
        projected = driving_minutes + span
        if projected > 11 * 60 and driving_minutes <= 11 * 60:
            over = projected - 11 * 60
            violations.append({
                "driver": driver, "date": date_text,
                "rule": "11-hour driving limit",
                "detail": "driving %.2f hours in this duty period, "
                          "%.2f hours over the 11-hour limit"
                          % (projected / 60.0, over / 60.0),
            })
        driving_minutes = projected
        driving_since_break += span
        days = 7 if limit == 60 else 8
        window_total = on_duty_in_window(date_text, days)
        if window_total > limit * 60 and date_text not in flagged["limit"]:
            violations.append({
                "driver": driver, "date": date_text,
                "rule": "%d-hour limit" % limit,
                "detail": "%.2f on-duty hours in %d days ending %s, "
                          "over the %d-hour limit"
                          % (window_total / 60.0, days, date_text, limit),
            })
            flagged["limit"].add(date_text)

    stats = {
        "driver": driver,
        "entries": len(rows),
        "driving_hours": hours(total_driving),
        "on_duty_hours": hours(total_on_duty),
        "violations": len(violations),
        "errors": len(errors),
    }
    return violations, errors, stats


def check_log(entries, limit):
    by_driver = {}
    for entry in entries:
        by_driver.setdefault(entry.get("driver", "unknown"), []).append(entry)
    result = {"drivers": [], "violations": [], "errors": []}
    for driver in sorted(by_driver):
        violations, errors, stats = check_driver(
            driver, by_driver[driver], limit)
        result["drivers"].append(stats)
        result["violations"].extend(violations)
        result["errors"].extend(errors)
    return result


def render_text(result):
    lines = ["ELD HOS check", "============="]
    for driver in result["drivers"]:
        lines.append(
            "%s: %d entries, %.2f driving hours, %.2f on-duty hours, "
            "%d violations, %d errors"
            % (driver["driver"], driver["entries"],
               driver["driving_hours"], driver["on_duty_hours"],
               driver["violations"], driver["errors"]))
    lines.append("")
    if result["violations"]:
        lines.append("Violations:")
        for item in result["violations"]:
            lines.append("  %s %s [%s] %s"
                         % (item["driver"], item["date"], item["rule"],
                            item["detail"]))
    else:
        lines.append("Violations: none")
    lines.append("")
    if result["errors"]:
        lines.append("Data errors:")
        for error in result["errors"]:
            lines.append("  " + error)
    else:
        lines.append("Data errors: none")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check ELD duty logs for Hours of Service violations.")
    parser.add_argument("log", help="JSON or CSV log file (list of entries)")
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable JSON")
    parser.add_argument("--limit", type=int, choices=(60, 70), default=70,
                        help="weekly on-duty limit in hours (default: 70)")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if any violation or error")
    args = parser.parse_args(argv)
    try:
        entries = load_entries(args.log)
    except (OSError, ValueError, csv.Error) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    result = check_log(entries, args.limit)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result), end="")
    if args.check and (result["violations"] or result["errors"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
