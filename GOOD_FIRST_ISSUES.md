# Good first issues

Small, testable tasks for first-time contributors. No network access needed.

1. Accept CSV log files in addition to JSON, with the same columns
   (driver, date, start, end, status). Scope: add a CSV loader,
   reuse the checker, add tests.
2. Add a `--driver` filter that checks only one driver from a
   multi-driver file. Scope: add the flag, filter before checking,
   add a CLI test.
3. Report the exact over-limit minutes for the 14-hour window the
   way the 11-hour rule already does. Scope: extend the detail
   string, add a unit test.
4. Add split-sleeper support: two breaks totalling 10 hours (one of
   at least 2 hours) pause the 14-hour clock. Scope: track paired
   breaks, add tests for the paired and unpaired cases, document it.
