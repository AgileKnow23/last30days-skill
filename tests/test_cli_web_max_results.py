"""CLI surface for --web-max-results and --investment-topic."""

from __future__ import annotations

import contextlib
import io
import unittest
import urllib.request
from unittest import mock

import last30days as cli
from lib import grounding, stocktwits


class WebMaxResultsFlagTests(unittest.TestCase):
    def test_flag_is_absent_by_default(self):
        args, _ = cli.build_parser().parse_known_args(["Generac"])
        self.assertIsNone(args.web_max_results)
        self.assertFalse(args.investment_topic)

    def test_flag_accepts_values_inside_the_bounds(self):
        for raw in ("1", "10", "20"):
            args, extra = cli.build_parser().parse_known_args(["--web-max-results", raw, "Generac"])
            self.assertEqual(int(raw), args.web_max_results)
            self.assertEqual([], extra)

    def test_flag_rejects_values_outside_the_bounds_or_non_integers(self):
        for raw in ("0", "21", "-3", "ten", "5.5"):
            with self.subTest(raw=raw):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                    cli.build_parser().parse_known_args(["--web-max-results", raw, "Generac"])
                self.assertIn("--web-max-results", err.getvalue())

    def test_flag_overrides_env_when_stashed_on_config(self):
        args, _ = cli.build_parser().parse_known_args(["--web-max-results", "3", "Generac"])
        config = {"LAST30DAYS_WEB_MAX_RESULTS": "15"}
        config[grounding.WEB_MAX_RESULTS_OVERRIDE_KEY] = args.web_max_results
        self.assertEqual(3, grounding.resolve_web_max_results(config))

    def test_help_documents_the_env_var_and_bounds(self):
        action = next(a for a in cli.build_parser()._actions if "--web-max-results" in a.option_strings)
        self.assertIn("LAST30DAYS_WEB_MAX_RESULTS", action.help)
        self.assertIn("1-20", action.help)
        self.assertIn("default 5", action.help)


class InvestmentTopicFlagTests(unittest.TestCase):
    def _run(self, argv, topic):
        args, _ = cli.build_parser().parse_known_args(argv)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli._check_investment_topic(args, topic)
        return code, err.getvalue()

    def test_strict_mode_rejects_a_ticker_first_topic_with_guidance(self):
        topic = "GNRC Generac material developments: SEC filings, earnings"
        code, err = self._run(["--investment-topic", topic], topic)
        self.assertEqual(2, code)
        self.assertIn("put the company name first", err)
        self.assertIn("Suggested topic: Generac $GNRC material developments affecting the investment thesis", err)

    def test_strict_mode_accepts_the_contract(self):
        topic = "Generac $GNRC material developments affecting the investment thesis"
        self.assertEqual((None, ""), self._run(["--investment-topic", topic], topic))

    def test_advisory_warns_on_financial_ticker_first_topics_without_blocking(self):
        topic = "WHR Whirlpool stock earnings"
        code, err = self._run([topic], topic)
        self.assertIsNone(code)
        self.assertIn("Warning:", err)
        self.assertIn("Whirlpool $WHR", err)

    def test_advisory_warns_on_a_cashtag_before_the_company_name(self):
        topic = "$WHR Whirlpool earnings"
        code, err = self._run([topic], topic)
        self.assertIsNone(code)
        self.assertIn("Warning:", err)
        self.assertIn("company name must come first", err)

    def test_advisory_never_warns_for_a_missing_cashtag_alone(self):
        # The StockTwits gate fires on everyday words, and the lane resolves a
        # company name or a crypto alias by itself, so a missing cashtag is
        # not worth a warning outside strict mode.
        for topic in (
            "Whirlpool stock earnings",
            "chicken stock recipe",
            "best stock photo sites",
            "Tesla stock",
            "bitcoin price",
        ):
            with self.subTest(topic=topic):
                self.assertEqual((None, ""), self._run([topic], topic))

    def test_advisory_leaves_a_crypto_alias_the_lane_resolves_alone(self):
        topic = "BTC price"
        self.assertEqual((None, ""), self._run([topic], topic))

    def test_strict_mode_accepts_a_short_all_caps_company_name(self):
        for topic in ("AMD $AMD earnings outlook", "IBM $IBM cloud outlook", "US Bancorp $USB deposits"):
            with self.subTest(topic=topic):
                self.assertEqual((None, ""), self._run(["--investment-topic", topic], topic))

    def test_strict_mode_still_rejects_a_ticker_repeated_before_the_name(self):
        topic = "WHR Whirlpool $WHR earnings"
        code, err = self._run(["--investment-topic", topic], topic)
        self.assertEqual(2, code)
        self.assertIn("put the company name first", err)
        self.assertIn("Suggested topic: Whirlpool $WHR", err)

    def test_non_financial_and_comparison_topics_are_untouched(self):
        for topic in ("Kanye West", "how to deploy on Fly.io", "Generac $GNRC vs Kohler $KOHL stock"):
            with self.subTest(topic=topic):
                self.assertEqual((None, ""), self._run([topic], topic))

    # Finding 12: a generic finance topic that happens to begin with an acronym is not a
    # ticker-first investment topic, and the advisory must not diagnose it as one.
    GENERIC_ACRONYM_TOPICS = (
        "US stock market today",
        "AI stock picks",
        "EV stock rally",
        "ETF dividend yield",
        "UK dividend stocks",
        "GDP and earnings correlation",
        "NFT crypto trends",
        "REIT dividend cuts",
        "CPI print stock reaction",
        "ESG dividend funds",
        "US Stock Market Today",
        "AI Stock Picks",
        "ETF Dividend Yield",
        "CPI Print And Stock Reaction",
    )
    # A bare head the structure cannot vouch for stays silent: one- and two-letter heads,
    # acronyms followed by ordinary words, and names that are their own symbol without a
    # cashtag. Strict mode still refuses every one of these; the advisory prefers silence.
    AMBIGUOUS_QUIET_TOPICS = (
        "US economy and stock valuations",
        "AI investment trends",
        "EV market outlook",
        "ETF fees compared",
        "UK market news",
        "GDP growth forecast",
        "NFT market activity",
        "REIT income trends",
        "CPI inflation report",
        "ESG investing debate",
        "CEO earnings commentary",
        "SEC market structure",
        "IBM earnings",
        "GE earnings",
        "T stock outlook",
        "F Ford earnings",
    )
    TICKER_THEN_NAME = {
        "WHR Whirlpool stock earnings": "Whirlpool $WHR",
        "GNRC Generac earnings outlook": "Generac $GNRC",
        "FCEL FuelCell Energy liquidity concerns": "FuelCell Energy $FCEL",
    }
    CASHTAG_FIRST = (
        "$WHR Whirlpool earnings",
        "$GNRC Generac guidance",
        "$FCEL FuelCell Energy liquidity",
    )
    ENTITY_FIRST = (
        "Whirlpool $WHR earnings outlook",
        "Generac $GNRC material developments",
        "FuelCell Energy $FCEL liquidity outlook",
        "AMD $AMD earnings outlook",
        "IBM $IBM cloud outlook",
        "US Bancorp $USB deposits",
    )

    def _run_offline(self, argv, topic):
        # The advisory must decide from the words alone: any attempt to reach StockTwits
        # or the network is a test failure, not a slow test.
        with mock.patch.object(stocktwits, "_get_json", side_effect=AssertionError("network")), \
             mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            return self._run(argv, topic)

    def test_advisory_stays_quiet_for_a_generic_topic_led_by_an_acronym(self):
        for topic in self.GENERIC_ACRONYM_TOPICS:
            with self.subTest(topic=topic):
                code, err = self._run_offline([topic], topic)
                self.assertIsNone(code)
                self.assertEqual("", err)

    def test_advisory_stays_quiet_when_a_bare_head_is_ambiguous(self):
        for topic in self.AMBIGUOUS_QUIET_TOPICS:
            with self.subTest(topic=topic):
                self.assertEqual((None, ""), self._run_offline([topic], topic))

    def test_advisory_warns_for_a_ticker_followed_by_the_name_it_abbreviates(self):
        for topic, suggestion in self.TICKER_THEN_NAME.items():
            with self.subTest(topic=topic):
                code, err = self._run_offline([topic], topic)
                self.assertIsNone(code)
                self.assertIn("Warning:", err)
                self.assertIn("bare ticker", err)
                self.assertIn(f"Suggested topic: {suggestion}", err)
                self.assertNotIn("Suggested topic: <Company Name>", err)

    def test_advisory_warns_for_a_cashtag_before_the_company_name(self):
        for topic in self.CASHTAG_FIRST:
            with self.subTest(topic=topic):
                code, err = self._run_offline([topic], topic)
                self.assertIsNone(code)
                self.assertIn("Warning:", err)
                self.assertIn("company name must come first", err)

    def test_advisory_stays_quiet_for_an_entity_first_topic(self):
        for topic in self.ENTITY_FIRST:
            with self.subTest(topic=topic):
                self.assertEqual((None, ""), self._run_offline([topic], topic))

    def test_strict_mode_is_not_weakened_by_the_advisory_rule(self):
        for topic in ("US stock market today", "ETF Dividend Yield", "WHR Whirlpool stock earnings", "F Ford earnings"):
            with self.subTest(topic=topic):
                code, err = self._run_offline(["--investment-topic", topic], topic)
                self.assertEqual(2, code)
                self.assertIn("Investment topic does not follow", err)
        for topic in self.ENTITY_FIRST:
            with self.subTest(topic=topic):
                self.assertEqual((None, ""), self._run_offline(["--investment-topic", topic], topic))

    def test_advisory_stays_quiet_for_a_contract_topic_with_a_long_objective(self):
        # Only ordering and the missing cashtag are worth a warning outside
        # strict mode; objective length is the adapter's call.
        topic = "Whirlpool $WHR " + " ".join(["stock"] * 14)
        self.assertEqual((None, ""), self._run([topic], topic))


if __name__ == "__main__":
    unittest.main()
