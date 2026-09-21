import copy
import json
import unittest
from pathlib import Path

from audit_history import audit
from prediction_events import template

ROOT = Path(__file__).resolve().parent.parent


class TooltipCopyTests(unittest.TestCase):
    def test_history_migrated_and_idempotent(self):
        history = json.loads((ROOT / 'docs/data/history.json').read_text())
        state = json.loads((ROOT / 'data/state/league-state.json').read_text())
        seed = json.loads((ROOT / 'data/seed/2026-09-21.json').read_text())
        self.assertEqual(history, audit(copy.deepcopy(history), state, seed))
        entries = [entry for s in history['snapshots']
                   for metrics in s.get('explanations', {}).values() for entry in metrics.values()]
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(entry['auditVersion'], 2)
            for jargon in ('sampling', 'replay', 'simulation', 'reconstruction', 'attribution'):
                self.assertNotIn(jargon, entry['text'].lower())

    def test_unknown_cause_not_invented(self):
        self.assertIn('No confirmed', template('standings_context', [], .02))

    def test_external_injury_does_not_promise_more_touches(self):
        text = template('external_opportunity', [
            {'kind': 'injury', 'player': 'Example', 'to': 'OUT', 'external': True}], .02)
        self.assertIn('depend on their roles', text)

    def test_named_transaction(self):
        text = template('roster_move', [{'kind': 'transaction',
            'footballFacts': ['GiantPunt added Kaelon Black.']}], .02)
        self.assertIn('GiantPunt added Kaelon Black.', text)
        self.assertIn('not guaranteed', text)


if __name__ == '__main__':
    unittest.main()
