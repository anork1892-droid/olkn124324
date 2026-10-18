"""Monetary source text must not silently turn into a smaller reward."""

import unittest

from bountyos.connectors import advertised_reward, qualify


class RewardTests(unittest.TestCase):
    def issue(self, reward_text):
        return {
            "title": "Documentation bounty: " + reward_text,
            "body": "Payment after the README correction is accepted.",
            "url": "https://github.com/example/project/issues/1",
            "repo": "example/project",
            "number": 1,
            "state": "OPEN",
            "authorAssociation": "OWNER",
        }

    def test_whole_amounts_and_explicit_currency_in_either_order(self):
        cases = [
            ("USD 1,500", 1500, "USD"),
            ("1,500 USD", 1500, "USD"),
            ("USD 1,500.50", 1500.50, "USD"),
            ("1,500.50 USD", 1500.50, "USD"),
            ("EUR 1.500,50", 1500.50, "EUR"),
            ("1.500,50 EUR", 1500.50, "EUR"),
            ("25,50 EUR", 25.50, "EUR"),
            ("USD 25.50", 25.50, "USD"),
            ("SEK 25,5", 25.50, "SEK"),
            ("1500.50 USD", 1500.50, "USD"),
            ("SEK 1 500,50", 1500.50, "SEK"),
            ("1\u00a0500 SEK", 1500, "SEK"),
            ("1\u202f500 SEK", 1500, "SEK"),
            ("USD 1,234,567", 1234567, "USD"),
            ("1.234.567 EUR", 1234567, "EUR"),
            ("USD 25.", 25, "USD"),
            ("USD 25, paid after merge", 25, "USD"),
            ("[25 USD]", 25, "USD"),
            ("25usd", 25, "USD"),
        ]
        for text, amount, currency in cases:
            with self.subTest(text=text):
                result = qualify(self.issue(text))
                self.assertEqual(result["reward_amount"], amount)
                self.assertEqual(result["reward_currency"], currency)
                self.assertEqual(result["status"], "qualified")
                self.assertTrue(result["source_facts"]["reward_currency_explicit"])

    def test_malformed_or_ambiguous_numbers_never_match_a_part(self):
        values = (
            "1.500", "0,125", "1,50.00", "1.50,00", "12,34,56",
            "1,500,00", "1,2345", "1.2345", "1,500.500", "1 50",
            "1'500", "1’500", "1e3", "1E+3", "1/2", "10-50",
            "10 – 50", "10 - 50", "10- 50", "10 -50", "-50", "−50", "+50", "25%",
        )
        for value in values:
            for text in ("USD " + value, value + " USD"):
                with self.subTest(text=text):
                    result = qualify(self.issue(text))
                    self.assertIsNone(result["reward_amount"])
                    self.assertNotEqual(result["status"], "qualified")

    def test_unknown_dollar_currency_is_not_promoted_to_usd(self):
        self.assertEqual(advertised_reward("Bounty $1,500"), (1500, "$"))
        self.assertEqual(advertised_reward("Bounty $25,50"), (25.50, "$"))
        result = qualify(self.issue("$1,500"))
        self.assertEqual(result["reward_amount"], 1500)
        self.assertEqual(result["reward_currency"], "$")
        self.assertEqual(result["status"], "qualified")
        self.assertFalse(result["source_facts"]["reward_currency_explicit"])
        self.assertFalse(result["source_facts"]["reward_verified_paid"])
        self.assertIsNone(advertised_reward("Bounty $1,23,45")[0])

    def test_invalid_explicit_amount_does_not_fall_back_to_other_dollars(self):
        result = qualify(self.issue("USD 1,23,45 (program previously offered $50)"))
        self.assertIsNone(result["reward_amount"])
        self.assertNotEqual(result["status"], "qualified")


if __name__ == "__main__":
    unittest.main()
