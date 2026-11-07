# eld-violation-checker

Paste ELD duty log rows in as JSON and get US Hours of Service
violations back, with the rule and the numbers behind each flag. It
runs fully locally: no account, no upload, no dependencies.

## What it checks

Property-carrying rules from FMCSA Part 395, computed from the rows
you supply:

- 11-hour driving limit: no more than 11 hours driving after 10
  consecutive hours off duty.
- 14-hour window: no driving after 14 hours since coming on duty
  after 10 consecutive hours off.
- 30-minute break: no driving after 8 cumulative hours of driving
  without at least 30 minutes off duty or in the sleeper berth.
- 60/70-hour limit: no more than 60 on-duty hours in 7 days, or 70
  in 8 days (pick with `--limit`, default 70).

A run of 10 or more consecutive hours OFF or in the sleeper berth
resets the 11-hour, 14-hour, and break counters. Overlapping entries,
unknown statuses, missing fields, and rows whose end is before their
start are reported as data errors, not violations. This tool is a log
checker, not legal advice.

## Input format

A JSON list of entries. Dates are `YYYY-MM-DD`, times are 24-hour
`HH:MM`, and status is one of `OFF`, `SB`, `DRIVING`, `ON`:

```json
[
  {"driver": "Jane Doe", "date": "2026-10-26",
   "start": "08:00", "end": "12:00", "status": "DRIVING"},
  {"driver": "Jane Doe", "date": "2026-10-26",
   "start": "12:00", "end": "12:30", "status": "ON"}
]
```

One date per row: a row that spans midnight should be split into two
rows at midnight, one per date.

## Usage

```sh
python3 eld_violation_checker.py examples/clean.json
python3 eld_violation_checker.py log.json --json
python3 eld_violation_checker.py log.json --limit 60
python3 eld_violation_checker.py log.json --check
```

`--check` exits non-zero when any violation or data error is found,
so it works as a gate in a script.

## Real example

Running it on `examples/clean.json` (a compliant day) gives:

```
ELD HOS check
=============
Jane Doe: 4 entries, 8.00 driving hours, 8.50 on-duty hours, 0 violations, 0 errors

Violations: none

Data errors: none
```

Running it on `examples/violations.json` (12 driving hours with no
30-minute break) gives:

```
ELD HOS check
=============
Jane Doe: 5 entries, 12.00 driving hours, 12.25 on-duty hours, 3 violations, 0 errors

Violations:
  Jane Doe 2026-10-26 [30-minute break] driving at 16:15 after 8.00 hours driving without a 30-minute break
  Jane Doe 2026-10-26 [30-minute break] driving at 19:15 after 11.00 hours driving without a 30-minute break
  Jane Doe 2026-10-26 [11-hour driving limit] driving 12.00 hours in this duty period, 1.00 hours over the 11-hour limit

Data errors: none
```

## Tests

```sh
python3 -m unittest discover -s tests
```

15 tests, covering a clean day, each rule on its own, the 10-hour
reset, the 60 and 70-hour limits, overlap and bad-status errors, and
the CLI exit codes.

## License

MIT. See `LICENSE`.
