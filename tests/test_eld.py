import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eld_violation_checker as eld

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def entry(driver, date, start, end, status):
    return {"driver": driver, "date": date, "start": start,
            "end": end, "status": status}


def rules(result):
    return [v["rule"] for v in result["violations"]]


class TestCleanLog(unittest.TestCase):
    def test_clean_day_has_no_violations(self):
        log = [
            entry("Jane", "2026-10-26", "08:00", "12:00", "DRIVING"),
            entry("Jane", "2026-10-26", "12:00", "12:30", "ON"),
            entry("Jane", "2026-10-26", "12:30", "13:00", "OFF"),
            entry("Jane", "2026-10-26", "13:00", "17:00", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["drivers"][0]["driving_hours"], 8.0)

    def test_midnight_spanning_row_is_an_error(self):
        # This format keeps one date per row, so a row whose end is
        # earlier than its start is a data error, not a silent rollover.
        log = [
            entry("Jane", "2026-10-26", "08:00", "12:00", "DRIVING"),
            entry("Jane", "2026-10-26", "12:00", "12:30", "ON"),
            entry("Jane", "2026-10-26", "12:30", "13:00", "OFF"),
            entry("Jane", "2026-10-26", "13:00", "17:00", "DRIVING"),
            entry("Jane", "2026-10-26", "17:00", "03:00", "OFF"),
            entry("Jane", "2026-10-27", "03:00", "07:00", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertIn("entry 4: end 03:00 is before start 17:00",
                      result["errors"])

    def test_reset_within_one_day(self):
        log = [
            entry("Jane", "2026-10-26", "00:00", "10:00", "OFF"),
            entry("Jane", "2026-10-26", "10:00", "14:00", "DRIVING"),
            entry("Jane", "2026-10-26", "14:00", "14:30", "OFF"),
            entry("Jane", "2026-10-26", "14:30", "18:30", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertEqual(result["violations"], [])


class TestViolations(unittest.TestCase):
    def test_eleven_hour_and_break_flags(self):
        log = [
            entry("Jane", "2026-10-26", "08:00", "12:00", "DRIVING"),
            entry("Jane", "2026-10-26", "12:00", "12:15", "ON"),
            entry("Jane", "2026-10-26", "12:15", "16:15", "DRIVING"),
            entry("Jane", "2026-10-26", "16:15", "19:15", "DRIVING"),
            entry("Jane", "2026-10-26", "19:15", "20:15", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertIn("11-hour driving limit", rules(result))
        self.assertIn("30-minute break", rules(result))

    def test_fourteen_hour_window_only(self):
        log = [
            entry("Jane", "2026-10-26", "06:00", "12:00", "ON"),
            entry("Jane", "2026-10-26", "12:00", "16:00", "DRIVING"),
            entry("Jane", "2026-10-26", "16:00", "19:00", "ON"),
            entry("Jane", "2026-10-26", "19:00", "20:30", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertEqual(rules(result), ["14-hour window"])

    def test_break_resets_after_thirty_minutes(self):
        log = [
            entry("Jane", "2026-10-26", "08:00", "12:00", "DRIVING"),
            entry("Jane", "2026-10-26", "12:00", "12:30", "SB"),
            entry("Jane", "2026-10-26", "12:30", "16:30", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertEqual(result["violations"], [])

    def test_seventy_hour_limit(self):
        # 8 days at 9 on-duty hours each, rolling into November so the
        # dates stay valid (October has 31 days).
        from datetime import date as day_date, timedelta
        log = []
        for offset in range(8):
            date = (day_date(2026, 10, 26) + timedelta(days=offset)).isoformat()
            log.append(entry("Jane", date, "08:00", "12:00", "DRIVING"))
            log.append(entry("Jane", date, "12:00", "12:30", "OFF"))
            log.append(entry("Jane", date, "12:30", "17:30", "DRIVING"))
        result = eld.check_log(log, 70)
        self.assertEqual(result["errors"], [])
        self.assertIn("70-hour limit", rules(result))

    def test_sixty_hour_limit_flag(self):
        from datetime import date as day_date, timedelta
        log = []
        for offset in range(7):
            date = (day_date(2026, 10, 26) + timedelta(days=offset)).isoformat()
            log.append(entry("Jane", date, "08:00", "12:00", "DRIVING"))
            log.append(entry("Jane", date, "12:00", "12:30", "OFF"))
            log.append(entry("Jane", date, "12:30", "17:30", "DRIVING"))
        result = eld.check_log(log, 60)
        self.assertEqual(result["errors"], [])
        self.assertIn("60-hour limit", rules(result))


class TestDataErrors(unittest.TestCase):
    def test_overlap_is_an_error(self):
        log = [
            entry("Jane", "2026-10-26", "08:00", "12:00", "DRIVING"),
            entry("Jane", "2026-10-26", "11:00", "13:00", "ON"),
        ]
        result = eld.check_log(log, 70)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("overlap", result["errors"][0])

    def test_unknown_status_is_an_error(self):
        log = [entry("Jane", "2026-10-26", "08:00", "12:00", "FLYING")]
        result = eld.check_log(log, 70)
        self.assertIn("unknown status", result["errors"][0])

    def test_missing_field_is_an_error(self):
        log = [{"driver": "Jane", "date": "2026-10-26",
                "start": "08:00", "status": "DRIVING"}]
        result = eld.check_log(log, 70)
        self.assertIn("missing field end", result["errors"][0])

    def test_unsorted_entries_are_sorted(self):
        log = [
            entry("Jane", "2026-10-26", "13:00", "17:00", "DRIVING"),
            entry("Jane", "2026-10-26", "08:00", "12:00", "DRIVING"),
        ]
        result = eld.check_log(log, 70)
        self.assertEqual(result["drivers"][0]["driving_hours"], 8.0)


class TestCli(unittest.TestCase):
    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "eld_violation_checker.py")]
            + args, capture_output=True, text=True)

    def test_example_files(self):
        clean = self.run_cli(
            [os.path.join(ROOT, "examples", "clean.json"), "--check"])
        self.assertEqual(clean.returncode, 0)
        self.assertIn("Violations: none", clean.stdout)
        bad = self.run_cli(
            [os.path.join(ROOT, "examples", "violations.json"), "--check"])
        self.assertEqual(bad.returncode, 1)
        self.assertIn("11-hour driving limit", bad.stdout)

    def test_json_output(self):
        proc = self.run_cli(
            [os.path.join(ROOT, "examples", "violations.json"), "--json"])
        data = json.loads(proc.stdout)
        self.assertGreater(len(data["violations"]), 0)
        self.assertEqual(data["drivers"][0]["driver"], "Jane Doe")

    def test_bad_file_exits_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            handle.write("not json")
            path = handle.name
        try:
            proc = self.run_cli([path])
            self.assertEqual(proc.returncode, 2)
        finally:
            os.unlink(path)


class TestCsvInput(unittest.TestCase):
    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "eld_violation_checker.py")]
            + args, capture_output=True, text=True)

    def test_csv_loader_matches_json_entries(self):
        from_csv = eld.load_csv(os.path.join(ROOT, "examples",
                                             "violations.csv"))
        from_json = eld.load_entries(os.path.join(ROOT, "examples",
                                                  "violations.json"))
        self.assertEqual(from_csv, from_json)
        self.assertEqual(
            eld.check_log(from_csv, 70), eld.check_log(from_json, 70))

    def test_cli_accepts_csv_file(self):
        proc = self.run_cli(
            [os.path.join(ROOT, "examples", "violations.csv"), "--check"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("11-hour driving limit", proc.stdout)

    def test_cli_csv_missing_column_exits_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False, newline="") as handle:
            handle.write("driver,date,start,end\n")
            handle.write("Jane Doe,2026-10-26,08:00,12:00\n")
            path = handle.name
        try:
            proc = self.run_cli([path])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("missing column", proc.stderr)
        finally:
            os.unlink(path)

    def test_cli_csv_without_header_exits_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False, newline="") as handle:
            path = handle.name
        try:
            proc = self.run_cli([path])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("header", proc.stderr)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
