"""Database timestamp parsing across Python versions.

PostgreSQL trims trailing zeros from fractional seconds, so the deployed
Python 3.9 runtime rejected values that 3.11+ accepts. These cases are the
exact shapes PostgREST returns; they must parse on every supported runtime.
"""
import unittest
from datetime import datetime, timedelta, timezone

from jobagent.mobile.moments import parse_moment


class ParseMomentTests(unittest.TestCase):
    def test_variable_fraction_widths_from_postgres(self):
        expected = datetime(2026, 9, 16, 13, 24, 9, tzinfo=timezone.utc)
        for value, microsecond in (
            ("2026-09-16T13:24:09.23869+00:00", 238690),   # reproduced production failure
            ("2026-09-16T13:24:09.113886+00:00", 113886),  # already-supported 6 digits
            ("2026-09-16T13:24:09.5+00:00", 500000),       # single digit
            ("2026-09-16T13:24:09.123+00:00", 123000),     # milliseconds
            ("2026-09-16T13:24:09.123456789+00:00", 123456),  # nanoseconds truncate
            ("2026-09-16T13:24:09+00:00", 0),              # no fraction
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_moment(value), expected.replace(microsecond=microsecond))

    def test_zulu_and_short_offset_forms(self):
        self.assertEqual(parse_moment("2026-09-16T13:24:09.23869Z"),
                         datetime(2026, 9, 16, 13, 24, 9, 238690, tzinfo=timezone.utc))
        self.assertEqual(parse_moment("2026-09-16T13:24:09Z").utcoffset(), timedelta(0))
        self.assertEqual(parse_moment("2026-09-16T13:24:09.5+04").utcoffset(), timedelta(hours=4))

    def test_offset_minutes_are_not_treated_as_a_fraction(self):
        self.assertEqual(parse_moment("2026-09-16T13:24:09.23869+05:30").utcoffset(),
                         timedelta(hours=5, minutes=30))

    def test_naive_timestamp_keeps_absent_offset_for_caller_checks(self):
        self.assertIsNone(parse_moment("2026-09-16T13:24:09.23869").tzinfo)

    def test_invalid_values_raise_value_error(self):
        for value in ("", "not-a-timestamp", "2026-13-99T99:99:99+00:00", 17, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_moment(value)


if __name__ == "__main__":
    unittest.main()
