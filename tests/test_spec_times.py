# -*- coding: utf-8 -*-
"""The shared TIME grammar: relative times and absolute dates (#598)."""

import datetime
import re
from unittest.mock import patch

import pytest

from phabfive.exceptions import PhabfiveInputException
from phabfive.spec.schema import TIME_PATTERN
from phabfive.spec.times import parse_absolute_time, parse_time, parse_time_with_unit

NOW = 1_782_900_000


@pytest.fixture(autouse=True)
def frozen_clock():
    with patch("phabfive.spec.times._now", return_value=NOW):
        yield


class TestAbsoluteDates:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-07-01", datetime.datetime(2026, 7, 1)),
            ("2026-07-01T09:30", datetime.datetime(2026, 7, 1, 9, 30)),
            ("2026-07-01 09:30:05", datetime.datetime(2026, 7, 1, 9, 30, 5)),
            ("  2026-07-01  ", datetime.datetime(2026, 7, 1)),
        ],
    )
    def test_a_date_is_read_as_local_time(self, value, expected):
        assert parse_absolute_time(value) == expected
        assert parse_time(value) == int(expected.timestamp())

    @pytest.mark.parametrize("value", ["7d", "14", 7, 1.5, "1h", None])
    def test_a_relative_time_is_not_a_date(self, value):
        assert parse_absolute_time(value) is None

    @pytest.mark.parametrize(
        "value",
        ["2026-13-01", "2026-02-30", "2026-7-1", "2026-07-01Z", "2026-07-01T25:00"],
    )
    def test_something_written_as_a_date_and_not_one_is_refused(self, value):
        with pytest.raises(PhabfiveInputException, match="Invalid date"):
            parse_time(value)

    def test_a_bare_date_is_its_midnight(self):
        stamp = parse_time("2026-07-01")

        assert datetime.datetime.fromtimestamp(stamp) == datetime.datetime(2026, 7, 1)


class TestRelativeTimesStillWork:
    @pytest.mark.parametrize(
        "value, days", [("7d", 7), ("2w", 14), ("14", 14), ("12h", 0.5), (3, 3)]
    )
    def test_a_relative_time_counts_back_from_now(self, value, days):
        assert parse_time(value) == NOW - round(days * 86400)

    def test_the_days_reader_is_unchanged(self):
        assert parse_time_with_unit("1w") == 7.0

    def test_the_error_names_the_date_spelling_too(self):
        with pytest.raises(PhabfiveInputException, match="2026-07-01"):
            parse_time("7%")

    def test_none_is_no_time(self):
        assert parse_time(None) is None


class TestThePublishedPattern:
    @pytest.mark.parametrize(
        "value",
        ["2026-07-01", "2026-07-01T09:30", "2026-07-01 09:30:05", " 2026-12-31 "],
    )
    def test_it_accepts_a_date(self, value):
        parse_time(value)

        assert re.match(TIME_PATTERN, value)

    @pytest.mark.parametrize(
        "value", ["2026-13-01", "2026-7-1", "2026-07-01Z", "2026-07-01T24:00"]
    )
    def test_it_refuses_what_the_parser_refuses(self, value):
        with pytest.raises(PhabfiveInputException):
            parse_time(value)

        assert not re.match(TIME_PATTERN, value)

    def test_a_day_the_month_lacks_is_the_one_shape_it_cannot_refuse(self):
        """Documented on TIME_PATTERN: the offline pass is what catches it."""
        from phabfive.spec.loader import parse_spec
        from phabfive.spec.validate import validate_offline

        assert re.match(TIME_PATTERN, "2026-02-30")

        problems = validate_offline(
            parse_spec(
                'kind: search\nsearch:\n  created-after: "2026-02-30"\n',
                format="yaml",
            )
        )

        assert [one.code for one in problems] == ["bad-time"]


class TestTheSearchSendsTheDate:
    def test_created_after_a_date_is_sent_as_its_epoch_second(self):
        from phabfive.maniphest.utils import time_constraint

        assert time_constraint("2026-07-01", "created-after") == int(
            datetime.datetime(2026, 7, 1).timestamp()
        )

    def test_the_key_is_named_in_the_error(self):
        from phabfive.maniphest.utils import time_constraint

        with pytest.raises(PhabfiveInputException, match="^created-after: Invalid"):
            time_constraint("2026-02-30", "created-after")


class TestAnUnquotedDateInASpec:
    """YAML and TOML read `2026-07-01` as a date object, not a string."""

    @pytest.mark.parametrize(
        "text, fmt",
        [
            ("kind: search\nsearch:\n  created-after: 2026-07-01\n", "yaml"),
            ('kind = "search"\n[search]\ncreated-after = 2026-07-01\n', "toml"),
            ("kind: search\nsearch:\n  closed-before: 2026-07-01 09:30:00\n", "yaml"),
        ],
    )
    def test_it_validates_clean(self, text, fmt):
        from phabfive.spec.loader import parse_spec
        from phabfive.spec.validate import validate_offline

        assert validate_offline(parse_spec(text, format=fmt)) == []

    def test_a_date_object_is_its_midnight(self):
        assert parse_time(datetime.date(2026, 7, 1)) == int(
            datetime.datetime(2026, 7, 1).timestamp()
        )

    def test_a_datetime_object_is_itself(self):
        moment = datetime.datetime(2026, 7, 1, 9, 30)

        assert parse_time(moment) == int(moment.timestamp())
