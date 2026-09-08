"""Investment-topic contract: <Company Name> $<TICKER> <short research objective>."""

from __future__ import annotations

import unittest

from lib import investment_topic as it


class ValidTopicTests(unittest.TestCase):
    def test_documented_examples_pass(self):
        for ticker, topic in it.EXAMPLES.items():
            with self.subTest(ticker=ticker):
                check = it.validate_investment_topic(topic)
                self.assertTrue(check.ok, check.problems)
                self.assertEqual(ticker, check.topic.ticker)
                self.assertEqual("material developments affecting the investment thesis", check.topic.objective)
        self.assertEqual("Generac", it.validate_investment_topic(it.EXAMPLES["GNRC"]).topic.company)
        self.assertEqual("FuelCell Energy", it.validate_investment_topic(it.EXAMPLES["FCEL"]).topic.company)
        self.assertEqual("Whirlpool", it.validate_investment_topic(it.EXAMPLES["WHR"]).topic.company)

    def test_short_objective_and_lowercase_cashtag_are_fine(self):
        check = it.validate_investment_topic("Whirlpool $whr tariff exposure")
        self.assertTrue(check.ok)
        self.assertEqual("WHR", check.topic.ticker)
        self.assertEqual("tariff exposure", check.topic.objective)

    def test_cashtag_only_topic_is_allowed_when_company_named(self):
        self.assertTrue(it.validate_investment_topic("Generac $GNRC").ok)

    def test_a_short_all_caps_company_name_is_not_a_ticker(self):
        cases = {
            "AMD $AMD earnings outlook": ("AMD", "AMD"),
            "IBM $IBM cloud outlook": ("IBM", "IBM"),
            "UPS $UPS volume outlook": ("UPS", "UPS"),
            "US Bancorp $USB deposits": ("US Bancorp", "USB"),
            "ASML $ASML lithography demand": ("ASML", "ASML"),
            "AMD, $AMD data center share": ("AMD", "AMD"),
        }
        for topic, (company, ticker) in cases.items():
            with self.subTest(topic=topic):
                check = it.validate_investment_topic(topic)
                self.assertTrue(check.ok, check.problems)
                self.assertEqual(company, check.topic.company)
                self.assertEqual(ticker, check.topic.ticker)

    def test_format_round_trips(self):
        topic = it.format_investment_topic("FuelCell Energy", "fcel")
        self.assertEqual(it.EXAMPLES["FCEL"], topic)
        self.assertEqual(topic, it.validate_investment_topic(topic).topic.render())


class InvalidTopicTests(unittest.TestCase):
    def test_ticker_first_topic_is_rejected_with_reorder_suggestion(self):
        check = it.validate_investment_topic(
            "GNRC Generac material developments: SEC filings, earnings, contracts"
        )
        self.assertFalse(check.ok)
        self.assertTrue(any("put the company name first" in p for p in check.problems))
        self.assertTrue(any("no $TICKER cashtag" in p for p in check.problems))
        self.assertEqual(it.EXAMPLES["GNRC"], check.suggestion)
        self.assertIn("Suggested topic: Generac $GNRC", check.message())

    def test_cashtag_first_topic_is_rejected(self):
        check = it.validate_investment_topic("$WHR Whirlpool earnings")
        self.assertFalse(check.ok)
        self.assertTrue(any("company name must come first" in p for p in check.problems))
        self.assertEqual(it.EXAMPLES["WHR"], check.suggestion)

    def test_missing_cashtag_is_rejected_and_no_ticker_is_invented(self):
        check = it.validate_investment_topic("Whirlpool earnings and dividend")
        self.assertFalse(check.ok)
        self.assertEqual(
            ("no $TICKER cashtag; without one StockTwits falls back to a name search that can miss the symbol",),
            check.problems,
        )
        self.assertIsNone(check.suggestion)
        self.assertTrue(it.validate_investment_topic("Whirlpool earnings and dividend", require_cashtag=False).ok)

    def test_angle_lists_belong_in_the_plan(self):
        check = it.validate_investment_topic(
            "Whirlpool $WHR material developments: SEC filings, earnings, ownership, consumer demand"
        )
        self.assertFalse(check.ok)
        self.assertTrue(any("--plan" in p for p in check.problems))
        self.assertEqual(it.EXAMPLES["WHR"], check.suggestion)

    def test_long_objective_is_rejected(self):
        objective = " ".join(["word"] * (it.MAX_OBJECTIVE_WORDS + 1))
        check = it.validate_investment_topic(f"Generac $GNRC {objective}")
        self.assertFalse(check.ok)
        self.assertTrue(any("keep the topic short" in p for p in check.problems))

    def test_multiple_cashtags_are_rejected(self):
        check = it.validate_investment_topic("Generac $GNRC versus Kohler $KOHL")
        self.assertFalse(check.ok)
        self.assertTrue(any("more than one cashtag" in p for p in check.problems))

    def test_looks_ticker_first(self):
        self.assertTrue(it.looks_ticker_first("WHR Whirlpool"))
        self.assertTrue(it.looks_ticker_first("$WHR Whirlpool"))
        self.assertTrue(it.looks_ticker_first("WHR Whirlpool $WHR earnings"))
        self.assertTrue(it.looks_ticker_first("NVDA Nvidia $nvda data center"))
        self.assertFalse(it.looks_ticker_first("Whirlpool $WHR"))
        self.assertFalse(it.looks_ticker_first("AMD $AMD earnings"))
        self.assertFalse(it.looks_ticker_first("US Bancorp $USB deposits"))
        self.assertFalse(it.looks_ticker_first("Kanye West"))
        self.assertFalse(it.looks_ticker_first(""))

    def test_ticker_repeated_as_a_later_cashtag_is_still_ticker_first(self):
        check = it.validate_investment_topic("WHR Whirlpool $WHR earnings")
        self.assertFalse(check.ok)
        self.assertTrue(any("put the company name first" in p for p in check.problems))
        self.assertEqual(it.EXAMPLES["WHR"], check.suggestion)

    def test_bare_ticker_without_a_cashtag_is_named_in_the_problem(self):
        check = it.validate_investment_topic("GNRC Generac outlook")
        self.assertFalse(check.ok)
        self.assertTrue(any("bare ticker 'GNRC'" in p and "put the company name first" in p for p in check.problems))
        self.assertEqual(it.EXAMPLES["GNRC"], check.suggestion)

    def test_parse_returns_none_without_a_leading_company(self):
        self.assertIsNone(it.parse_investment_topic("$GNRC thesis"))
        self.assertIsNone(it.parse_investment_topic("Generac thesis"))


if __name__ == "__main__":
    unittest.main()
