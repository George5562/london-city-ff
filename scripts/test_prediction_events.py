import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from prediction_events import deterministic_cause, diff_states, explain_prediction_moves


class PredictionEventTests(unittest.TestCase):
    def setUp(self):
        self.previous_key = os.environ.pop("OPENROUTER_API_KEY", None)

    def tearDown(self):
        if self.previous_key is not None:
            os.environ["OPENROUTER_API_KEY"] = self.previous_key

    def test_roster_move_is_detected(self):
        before = {"players": {"1": {"name": "A", "team": 1, "slot": 20, "pro": 1,
            "injury": "ACTIVE", "stats": {"actual": None, "projection": 10}}}, "transactions": {}}
        after = {"players": {"1": {"name": "A", "team": 2, "slot": 2, "pro": 1,
            "injury": "ACTIVE", "stats": {"actual": None, "projection": 10}}}, "transactions": {}}
        events = diff_states(before, after)
        self.assertEqual(events[0]["kind"], "roster_move")
        self.assertEqual(deterministic_cause(events), "roster_move")

    def test_injury_to_teammate_is_external_opportunity(self):
        events = [{"kind": "injury", "player": "Backup", "pro": 7, "team": None,
                   "from": "ACTIVE", "to": "OUT", "external": True}]
        self.assertEqual(deterministic_cause(events), "external_opportunity")

    def test_material_move_has_fallback_tooltip_without_api_key(self):
        before_result = {"teams": [{"id": 1, "champ": .10}]}
        after_result = {"teams": [{"id": 1, "champ": .12}]}
        state = {"players": {"1": {"name": "A", "team": 1, "slot": 2, "pro": 1,
            "injury": "ACTIVE", "stats": {"actual": None, "projection": 10}}}, "transactions": {}}
        changed = {"players": {"1": {"name": "A", "team": 1, "slot": 2, "pro": 1,
            "injury": "ACTIVE", "stats": {"actual": None, "projection": 13}}}, "transactions": {}}
        payload = explain_prediction_moves(before_result, after_result, state, changed)
        self.assertEqual(payload["1"]["cause"], "projection_rerating")
        self.assertEqual(payload["1"]["writtenBy"], "template")
        self.assertIn("10 to 13 fantasy points", payload["1"]["text"])
        self.assertIn("raising their expected scoring contribution", payload["1"]["text"])


if __name__ == "__main__":
    unittest.main()
