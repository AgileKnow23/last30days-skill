"""Serper date provenance and the bounded web result count.

Serper labels fresh Google hits relatively ("5 days ago") and some not at
all. These tests pin how each label kind is classified against an injected
retrieval instant, that unknown dates never enter the window-gated grounding
stream, and that the per-subquery result count follows CLI > env > default.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from datetime import datetime, timezone
from unittest import mock

from lib import dates, grounding, schema

WINDOW = ("2026-08-08", "2026-09-07")
NOW = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)


class RelativeDateParsingTests(unittest.TestCase):
    def test_plural_and_singular_units_resolve_against_now(self):
        cases = {
            "5 days ago": ("2026-09-02", "day"),
            "1 day ago": ("2026-09-06", "day"),
            "10 hours ago": ("2026-09-07", "day"),
            "an hour ago": ("2026-09-07", "day"),
            "45 minutes ago": ("2026-09-07", "day"),
            "2 weeks ago": ("2026-08-24", "week"),
            "one week ago": ("2026-08-31", "week"),
            "1 month ago": ("2026-08-08", "month"),
            "3 months ago": ("2026-06-09", "month"),
        }
        for text, (expected, precision) in cases.items():
            with self.subTest(text=text):
                relative = dates.parse_relative_date(text, now=NOW)
                self.assertIsNotNone(relative)
                self.assertEqual(expected, relative.iso)
                self.assertEqual(precision, relative.precision)
                self.assertEqual(NOW, relative.retrieved_at)

    def test_hours_that_cross_midnight_land_on_the_previous_utc_day(self):
        relative = dates.parse_relative_date("20 hours ago", now=NOW)
        self.assertEqual("2026-09-06", relative.iso)

    def test_interval_carries_rounding_slack(self):
        # A label is rounded down, so the earliest plausible date is one whole
        # unit before the point estimate: "2 weeks ago" reaches back to 21 days.
        relative = dates.parse_relative_date("2 weeks ago", now=NOW)
        self.assertEqual("2026-08-24", relative.iso)
        self.assertEqual("2026-08-17", relative.earliest.isoformat())
        self.assertTrue(relative.within_window(*WINDOW))
        month = dates.parse_relative_date("1 month ago", now=NOW)
        self.assertEqual(("2026-08-08", "2026-07-09"), (month.iso, month.earliest.isoformat()))
        boundary = dates.parse_relative_date("4 weeks ago", now=NOW)
        # Latest plausible date is inside the window, earliest is not.
        self.assertEqual("2026-08-10", boundary.iso)
        self.assertFalse(boundary.within_window(*WINDOW))

    def test_week_slack_is_a_whole_week_at_a_custom_window_edge(self):
        # "2 weeks ago" can be 20.99 days old, which is 2026-08-17 at this NOW;
        # a window that opens on the 18th must not admit it, one that opens on
        # the 17th may.
        relative = dates.parse_relative_date("2 weeks ago", now=NOW)
        self.assertFalse(relative.within_window("2026-08-18", "2026-09-07"))
        self.assertTrue(relative.within_window("2026-08-17", "2026-09-07"))

    def test_non_relative_and_malformed_labels_return_none(self):
        for text in ("", None, "Aug 27, 2026", "2026-08-27", "yesterday", "0 days ago", "days ago", "5 fortnights ago"):
            with self.subTest(text=text):
                self.assertIsNone(dates.parse_relative_date(text, now=NOW))

    def test_naive_now_is_taken_as_utc(self):
        relative = dates.parse_relative_date("1 day ago", now=datetime(2026, 9, 7, 18, 0))
        self.assertEqual("2026-09-06", relative.iso)
        self.assertEqual(timezone.utc, relative.retrieved_at.tzinfo)

    def test_confidence_is_never_high_for_derived_dates(self):
        self.assertEqual("med", dates.relative_date_confidence(dates.parse_relative_date("5 days ago", now=NOW), *WINDOW))
        self.assertEqual("low", dates.relative_date_confidence(dates.parse_relative_date("4 weeks ago", now=NOW), *WINDOW))
        self.assertEqual("low", dates.relative_date_confidence(dates.parse_relative_date("1 month ago", now=NOW), *WINDOW))


class ResolveResultDateTests(unittest.TestCase):
    def test_absolute_in_window_is_source_absolute_high(self):
        resolved = grounding.resolve_result_date("Aug 27, 2026", WINDOW, retrieved_at=NOW)
        self.assertIsNone(resolved.drop_reason)
        self.assertEqual("2026-08-27", resolved.published_at)
        self.assertEqual(schema.DATE_PROVENANCE_SOURCE_ABSOLUTE, resolved.provenance)
        self.assertEqual("high", resolved.confidence)
        self.assertEqual({"date_provenance": "source_absolute", "date_source_text": "Aug 27, 2026"}, resolved.metadata())

    def test_absolute_boundaries_are_inclusive(self):
        for text in ("2026-08-08", "2026-09-07", "Aug 8, 2026", "September 7, 2026"):
            with self.subTest(text=text):
                self.assertIsNone(grounding.resolve_result_date(text, WINDOW, retrieved_at=NOW).drop_reason)

    def test_absolute_out_of_window_is_dropped_with_reason(self):
        resolved = grounding.resolve_result_date("2026-08-07", WINDOW, retrieved_at=NOW)
        self.assertEqual(grounding.DROP_OUT_OF_WINDOW, resolved.drop_reason)
        self.assertEqual("2026-08-07", resolved.published_at)

    def test_relative_in_window_is_derived_with_retrieval_provenance(self):
        resolved = grounding.resolve_result_date("5 days ago", WINDOW, retrieved_at=NOW)
        self.assertIsNone(resolved.drop_reason)
        self.assertEqual("2026-09-02", resolved.published_at)
        self.assertEqual(schema.DATE_PROVENANCE_DERIVED_RELATIVE, resolved.provenance)
        self.assertEqual("med", resolved.confidence)
        self.assertEqual(
            {
                "date_provenance": "derived_relative",
                "date_source_text": "5 days ago",
                "date_precision": "day",
                "date_retrieved_at": "2026-09-07T18:00:00+00:00",
            },
            resolved.metadata(),
        )

    def test_relative_straddling_the_window_is_dropped_not_claimed_fresh(self):
        for text in ("4 weeks ago", "1 month ago", "31 days ago"):
            with self.subTest(text=text):
                resolved = grounding.resolve_result_date(text, WINDOW, retrieved_at=NOW)
                self.assertEqual(grounding.DROP_RELATIVE_UNVERIFIABLE, resolved.drop_reason)
                self.assertEqual("low", resolved.confidence)

    def test_week_label_is_dropped_when_its_whole_week_does_not_fit_a_custom_window(self):
        twenty_days = ("2026-08-18", "2026-09-07")
        resolved = grounding.resolve_result_date("2 weeks ago", twenty_days, retrieved_at=NOW)
        self.assertEqual(grounding.DROP_RELATIVE_UNVERIFIABLE, resolved.drop_reason)
        kept = grounding.resolve_result_date("2 weeks ago", ("2026-08-17", "2026-09-07"), retrieved_at=NOW)
        self.assertIsNone(kept.drop_reason)
        self.assertEqual("med", kept.confidence)

    def test_relative_exactly_at_window_start_is_dropped_because_slack_exceeds_it(self):
        resolved = grounding.resolve_result_date("30 days ago", WINDOW, retrieved_at=NOW)
        self.assertEqual("2026-08-08", resolved.published_at)
        self.assertEqual(grounding.DROP_RELATIVE_UNVERIFIABLE, resolved.drop_reason)

    def test_missing_and_malformed_labels_are_unknown_and_dropped(self):
        missing = grounding.resolve_result_date("", WINDOW, retrieved_at=NOW)
        self.assertEqual(grounding.DROP_UNDATED, missing.drop_reason)
        self.assertEqual(schema.DATE_PROVENANCE_UNKNOWN, missing.provenance)
        self.assertIsNone(missing.published_at)
        malformed = grounding.resolve_result_date("last Tuesday", WINDOW, retrieved_at=NOW)
        self.assertEqual(grounding.DROP_UNPARSABLE, malformed.drop_reason)
        self.assertEqual(schema.DATE_PROVENANCE_UNKNOWN, malformed.provenance)
        self.assertIsNone(grounding.resolve_result_date(None, WINDOW, retrieved_at=NOW).published_at)

    def test_legacy_absolute_parser_still_ignores_relative_labels(self):
        self.assertEqual("2026-08-27", grounding._parse_serper_date("Aug 27, 2026"))
        self.assertIsNone(grounding._parse_serper_date("5 days ago"))


def _serper_payload():
    return {
        "organic": [
            {"title": "absolute", "link": "https://a.example/1", "snippet": "s", "date": "Aug 27, 2026"},
            {"title": "relative", "link": "https://a.example/2", "snippet": "s", "date": "5 days ago"},
            {"title": "undated", "link": "https://a.example/3", "snippet": "s"},
            {"title": "stale", "link": "https://a.example/4", "snippet": "s", "date": "Jul 1, 2026"},
            {"title": "fuzzy", "link": "https://a.example/5", "snippet": "s", "date": "1 month ago"},
            {"title": "garbage", "link": "https://a.example/6", "snippet": "s", "date": "soon"},
            {"title": "beyond-count", "link": "https://a.example/7", "snippet": "s", "date": "Aug 28, 2026"},
        ]
    }


class SerperSearchTests(unittest.TestCase):
    def test_keeps_absolute_and_derived_dates_and_accounts_for_every_drop(self):
        with mock.patch.object(grounding.http, "request", return_value=_serper_payload()) as request:
            items, artifact = grounding.serper_search("q", WINDOW, "fake-key", count=6, retrieved_at=NOW)

        self.assertEqual(6, request.call_args.kwargs["json_data"]["num"])
        self.assertEqual(["absolute", "relative"], [item["title"] for item in items])
        absolute, relative = items
        self.assertEqual("2026-08-27", absolute["date"])
        self.assertEqual("high", absolute["date_confidence"])
        self.assertEqual("source_absolute", absolute["metadata"]["date_provenance"])
        self.assertEqual("2026-09-02", relative["date"])
        self.assertEqual("med", relative["date_confidence"])
        self.assertEqual("derived_relative", relative["metadata"]["date_provenance"])
        self.assertEqual("5 days ago", relative["metadata"]["date_source_text"])
        self.assertEqual("2026-09-07T18:00:00+00:00", relative["metadata"]["date_retrieved_at"])
        self.assertEqual(
            {
                "label": "serper",
                "webSearchQueries": ["q"],
                "resultCount": 2,
                "organicCount": 6,
                "requestedCount": 6,
                "retrievedAt": "2026-09-07T18:00:00+00:00",
                "derivedRelativeCount": 1,
                "dropped": {
                    "undated": 1,
                    "unparsable_date": 1,
                    "out_of_window": 1,
                    "relative_unverifiable": 1,
                },
            },
            artifact,
        )

    def test_count_is_bounded_before_the_request(self):
        with mock.patch.object(grounding.http, "request", return_value={"organic": []}) as request:
            grounding.serper_search("q", WINDOW, "fake-key", count=500, retrieved_at=NOW)
            grounding.serper_search("q", WINDOW, "fake-key", count=0, retrieved_at=NOW)
        nums = [call.kwargs["json_data"]["num"] for call in request.call_args_list]
        self.assertEqual([grounding.WEB_MAX_RESULTS_MAX, grounding.WEB_MAX_RESULTS_MIN], nums)

    def test_derived_dates_survive_normalization_with_provenance(self):
        from lib import normalize

        with mock.patch.object(grounding.http, "request", return_value=_serper_payload()):
            items, _ = grounding.serper_search("q", WINDOW, "fake-key", count=6, retrieved_at=NOW)
        normalized = normalize.normalize_source_items("grounding", items, *WINDOW)
        by_title = {item.title: item for item in normalized}
        self.assertEqual({"absolute", "relative"}, set(by_title))
        self.assertEqual("high", by_title["absolute"].date_confidence)
        self.assertEqual("source_absolute", schema.date_provenance(by_title["absolute"]))
        self.assertEqual("med", by_title["relative"].date_confidence)
        self.assertEqual("derived_relative", schema.date_provenance(by_title["relative"]))
        self.assertTrue(all(schema.freshness_verifiable(item) for item in normalized))


class WebMaxResultsTests(unittest.TestCase):
    def setUp(self):
        grounding._WEB_MAX_RESULTS_WARNED.clear()

    def _resolve(self, config):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            value = grounding.resolve_web_max_results(config)
        return value, err.getvalue()

    def test_default_is_five(self):
        self.assertEqual((5, ""), self._resolve({}))
        self.assertEqual((5, ""), self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": ""}))

    def test_env_value_is_honored(self):
        self.assertEqual((10, ""), self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": "10"}))
        self.assertEqual((1, ""), self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": " 1 "}))

    def test_env_out_of_range_is_clamped_with_one_warning(self):
        value, err = self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": "99"})
        self.assertEqual(20, value)
        self.assertIn("LAST30DAYS_WEB_MAX_RESULTS='99' is outside 1-20; using 20", err)
        value, err = self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": "99"})
        self.assertEqual((20, ""), (value, err))
        self.assertEqual(1, self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": "0"})[0])

    def test_env_garbage_falls_back_to_default_with_warning(self):
        value, err = self._resolve({"LAST30DAYS_WEB_MAX_RESULTS": "ten"})
        self.assertEqual(5, value)
        self.assertIn("not an integer; using 5", err)

    def test_cli_override_beats_env(self):
        config = {"LAST30DAYS_WEB_MAX_RESULTS": "10", "_web_max_results": 3}
        self.assertEqual((3, ""), self._resolve(config))
        self.assertEqual(20, self._resolve({"_web_max_results": 400})[0])

    def test_web_search_passes_the_resolved_count_and_retrieval_time_to_serper(self):
        config = {"SERPER_API_KEY": "fake-key", "LAST30DAYS_WEB_MAX_RESULTS": "8", "EXCLUDE_SOURCES": "reddit"}
        with mock.patch.object(grounding, "serper_search", return_value=([], {})) as serper:
            grounding.web_search("q", WINDOW, config, backend="serper", retrieved_at=NOW)
        self.assertEqual(8, serper.call_args.kwargs["count"])
        self.assertEqual(NOW, serper.call_args.kwargs["retrieved_at"])

    def test_web_search_passes_the_resolved_count_to_brave(self):
        config = {"BRAVE_API_KEY": "fake-key", "_web_max_results": 12, "EXCLUDE_SOURCES": "reddit"}
        with mock.patch.object(grounding, "brave_search", return_value=([], {})) as brave:
            grounding.web_search("q", WINDOW, config)
        self.assertEqual(12, brave.call_args.kwargs["count"])


class ProvenanceHelpersTests(unittest.TestCase):
    def _item(self, published_at, **metadata):
        return schema.SourceItem(
            item_id="i", source="grounding", title="t", body="b", url="https://e.example",
            published_at=published_at, metadata=metadata,
        )

    def test_undated_items_are_unknown_regardless_of_metadata(self):
        item = self._item(None, date_provenance="source_absolute")
        self.assertEqual("unknown", schema.date_provenance(item))
        self.assertFalse(schema.freshness_verifiable(item))
        self.assertEqual("unknown", schema.date_provenance(None))

    def test_dated_items_default_to_source_absolute(self):
        self.assertEqual("source_absolute", schema.date_provenance(self._item("2026-09-01")))
        self.assertEqual("derived_relative", schema.date_provenance(self._item("2026-09-01", date_provenance="derived_relative")))
        self.assertTrue(schema.freshness_verifiable(self._item("2026-09-01")))

    def test_unknown_dates_score_zero_freshness_in_every_mode(self):
        from lib import signals

        item = self._item(None)
        for mode in ("strict_recent", "balanced_recent", "evergreen_ok"):
            score = signals.freshness(item, mode, reference_date="2026-09-07")
            expected = {"strict_recent": 0, "balanced_recent": 10, "evergreen_ok": 40}[mode]
            self.assertEqual(expected, score, mode)
        self.assertEqual(0, signals.freshness(item, "strict_recent", reference_date="2026-09-07"))


if __name__ == "__main__":
    unittest.main()
