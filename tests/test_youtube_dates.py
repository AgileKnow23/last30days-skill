"""YouTube publication dates: preserved when a provider supplies one, derived
only from a relative label against an explicit retrieval instant, never
inferred otherwise.

Provider shapes covered: yt-dlp search (upload_date YYYYMMDD, epoch
timestamp), ScrapeCreators search (publishedTime ISO 8601 with milliseconds,
publishedTimeText relative), plus date-only, missing, and malformed values.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from lib import normalize, schema, youtube_yt

FROM, TO = "2026-08-08", "2026-09-07"
NOW = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)


class ResolveVideoDateTests(unittest.TestCase):
    def test_ytdlp_upload_date_yyyymmdd(self):
        published, fields = youtube_yt.resolve_video_date({"upload_date": "20260820"}, FROM, TO)
        self.assertEqual("2026-08-20", published)
        self.assertEqual({"date_provenance": "source_absolute", "date_source_text": "20260820"}, fields)

    def test_ytdlp_epoch_timestamp_when_upload_date_missing(self):
        published, fields = youtube_yt.resolve_video_date({"timestamp": 1788220800}, FROM, TO)
        self.assertEqual("2026-09-01", published)
        self.assertEqual("source_absolute", fields["date_provenance"])

    def test_scrapecreators_full_iso_timestamp_with_milliseconds(self):
        raw = {"publishedTime": "2026-08-20T17:08:46.499Z", "publishedTimeText": "2 weeks ago"}
        published, fields = youtube_yt.resolve_video_date(raw, FROM, TO, retrieved_at=NOW)
        self.assertEqual("2026-08-20", published)
        self.assertEqual("source_absolute", fields["date_provenance"])
        self.assertEqual("2026-08-20T17:08:46.499Z", fields["date_source_text"])
        self.assertNotIn("date_confidence", fields)

    def test_date_only_value(self):
        published, fields = youtube_yt.resolve_video_date({"date": "2026-08-20"}, FROM, TO)
        self.assertEqual("2026-08-20", published)
        self.assertEqual("source_absolute", fields["date_provenance"])

    def test_missing_date_is_unknown_and_not_invented(self):
        raw = {"id": "x", "title": "t", "view_count": 999999, "like_count": 5000}
        published, fields = youtube_yt.resolve_video_date(raw, FROM, TO, retrieved_at=NOW)
        self.assertIsNone(published)
        self.assertEqual({"date_provenance": "unknown"}, fields)

    def test_malformed_values_are_unknown_with_the_label_kept(self):
        for raw in ({"upload_date": "20261345"}, {"publishedTime": "not a date"}, {"date": "13/45/2026"}, {"timestamp": "soon"}, {"upload_date": True}):
            with self.subTest(raw=raw):
                published, fields = youtube_yt.resolve_video_date(raw, FROM, TO, retrieved_at=NOW)
                self.assertIsNone(published)
                self.assertEqual("unknown", fields["date_provenance"])
        _, fields = youtube_yt.resolve_video_date({"upload_date": "20261345"}, FROM, TO)
        self.assertEqual("20261345", fields["date_source_text"])

    def test_relative_published_time_text_is_derived_against_retrieval_time(self):
        published, fields = youtube_yt.resolve_video_date({"publishedTimeText": "2 weeks ago"}, FROM, TO, retrieved_at=NOW)
        self.assertEqual("2026-08-24", published)
        self.assertEqual(
            {
                "date_provenance": "derived_relative",
                "date_source_text": "2 weeks ago",
                "date_precision": "week",
                "date_retrieved_at": "2026-09-07T18:00:00+00:00",
                "date_confidence": "med",
            },
            fields,
        )

    def test_relative_label_near_the_window_edge_is_low_confidence(self):
        _, fields = youtube_yt.resolve_video_date({"publishedTimeText": "1 month ago"}, FROM, TO, retrieved_at=NOW)
        self.assertEqual("derived_relative", fields["date_provenance"])
        self.assertEqual("low", fields["date_confidence"])

    def test_malformed_absolute_falls_back_to_relative_label(self):
        raw = {"publishedTime": "garbage", "publishedTimeText": "3 days ago"}
        published, fields = youtube_yt.resolve_video_date(raw, FROM, TO, retrieved_at=NOW)
        self.assertEqual("2026-09-04", published)
        self.assertEqual("derived_relative", fields["date_provenance"])


class ProviderPathTests(unittest.TestCase):
    def setUp(self):
        youtube_yt.reset_search_cache()

    def _sc_video(self, **overrides):
        video = {
            "type": "video",
            "id": "vid1",
            "url": "https://www.youtube.com/watch?v=vid1",
            "title": "Generac Q2 call",
            "channel": {"id": "c", "title": "Chan", "handle": "@chan"},
            "viewCountInt": 1200,
            "publishedTimeText": "2 weeks ago",
            "publishedTime": "2026-08-24T10:00:00.000Z",
            "lengthSeconds": 600,
        }
        video.update(overrides)
        return video

    def _sc_items(self, video):
        with mock.patch.object(youtube_yt, "_sc_youtube_search", return_value=[video]):
            result = youtube_yt.search_youtube_sc("Generac $GNRC", FROM, TO, depth="quick", token="dummy-token")
        return result["items"]

    def test_scrapecreators_search_preserves_the_absolute_date(self):
        item = self._sc_items(self._sc_video())[0]
        self.assertEqual("2026-08-24", item["date"])
        self.assertEqual("source_absolute", item["date_provenance"])
        self.assertEqual("2026-08-24T10:00:00.000Z", item["date_source_text"])

    def test_scrapecreators_search_without_any_date_stays_unknown(self):
        video = self._sc_video()
        del video["publishedTime"]
        del video["publishedTimeText"]
        item = self._sc_items(video)[0]
        self.assertIsNone(item["date"])
        self.assertEqual("unknown", item["date_provenance"])

    def test_scrapecreators_relative_only_is_marked_derived(self):
        video = self._sc_video()
        del video["publishedTime"]
        item = self._sc_items(video)[0]
        self.assertIsNotNone(item["date"])
        self.assertEqual("derived_relative", item["date_provenance"])
        self.assertEqual("week", item["date_precision"])

    def test_ytdlp_search_preserves_upload_date_and_provenance(self):
        from lib.subproc import SubprocResult

        stdout = json.dumps({"id": "abc", "title": "t", "upload_date": "20260820", "view_count": 1}) + "\n" \
            + json.dumps({"id": "def", "title": "t2", "view_count": 1}) + "\n"
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=SubprocResult(returncode=0, stdout=stdout, stderr="")):
            result = youtube_yt.search_youtube("Generac", FROM, TO, depth="quick")
        by_id = {item["video_id"]: item for item in result["items"]}
        self.assertEqual("2026-08-20", by_id["abc"]["date"])
        self.assertEqual("source_absolute", by_id["abc"]["date_provenance"])
        self.assertIsNone(by_id["def"]["date"])
        self.assertEqual("unknown", by_id["def"]["date_provenance"])


class NormalizationTests(unittest.TestCase):
    def _normalized(self, item):
        return normalize.normalize_source_items("youtube", [item], FROM, TO)[0]

    def test_provenance_travels_into_metadata_and_export(self):
        derived = self._normalized({
            "video_id": "v", "title": "t", "url": "https://www.youtube.com/watch?v=v",
            "date": "2026-08-24", "date_provenance": "derived_relative", "date_source_text": "2 weeks ago",
            "date_precision": "week", "date_retrieved_at": "2026-09-07T18:00:00+00:00", "date_confidence": "med",
        })
        self.assertEqual("med", derived.date_confidence)
        self.assertEqual("derived_relative", schema.date_provenance(derived))
        self.assertEqual("2 weeks ago", derived.metadata["date_source_text"])

    def test_unknown_youtube_date_is_kept_but_never_freshness_verifiable(self):
        from lib import signals

        unknown = self._normalized({
            "video_id": "v", "title": "t", "url": "https://www.youtube.com/watch?v=v",
            "date": None, "date_provenance": "unknown",
        })
        self.assertIsNone(unknown.published_at)
        self.assertEqual("unknown", schema.date_provenance(unknown))
        self.assertFalse(schema.freshness_verifiable(unknown))
        self.assertEqual(0, signals.freshness(unknown, "strict_recent", reference_date=TO))
        candidate = schema.Candidate(
            candidate_id="c", item_id="v", source="youtube", title="t", url=unknown.url,
            snippet="", subquery_labels=[], native_ranks={}, local_relevance=0.5, freshness=0,
            engagement=0, source_quality=0.5, rrf_score=0.0, final_score=1.0, cluster_id=None,
            source_items=[unknown], metadata={"range_from": FROM, "range_to": TO},
        )
        self.assertEqual("unknown", schema.to_agent_export(_report_with(candidate))["results"][0]["date_provenance"])


def _report_with(candidate: schema.Candidate) -> schema.Report:
    return schema.Report(
        topic="Generac $GNRC", range_from=FROM, range_to=TO, generated_at="2026-09-07T18:00:00Z",
        provider_runtime=schema.ProviderRuntime(reasoning_provider="none", planner_model="none", rerank_model="none"),
        query_plan=schema.QueryPlan(intent="factual", freshness_mode="strict_recent", cluster_mode="none",
                                    raw_topic="Generac $GNRC", subqueries=[], source_weights={}),
        clusters=[], ranked_candidates=[candidate], items_by_source={}, errors_by_source={},
    )


if __name__ == "__main__":
    unittest.main()
