import unittest
from copy import deepcopy
from timeline_events import collect


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.state = {'players': {'7': {'name': 'Player', 'team': 1, 'slot': 2,
            'pro': 1, 'injury': 'ACTIVE', 'stats': {}}}, 'transactions': {}}
        self.old = {'teams': [{'id': 1, 'name': 'Team A', 'champ': .2}, {'id': 2, 'name': 'Team B', 'champ': .1}]}
        self.new = deepcopy(self.old)
        self.new['teams'][0]['champ'] = .18

    def test_injury_carries_observed_odds(self):
        after = deepcopy(self.state)
        after['players']['7']['injury'] = 'OUT'
        events = collect(self.state, after, self.old, self.new, 'now')
        self.assertEqual(events[0]['kind'], 'I')
        self.assertEqual(events[0]['changes'][0], {'team': 1, 'before': .2, 'after': .18})

    def test_declined_trade_excluded_and_completed_trade_included(self):
        after = deepcopy(self.state)
        after['transactions']['t'] = {'type': 'TRADE_DECLINE', 'status': 'EXECUTED',
            'items': [{'fromTeamId': 1, 'toTeamId': 2, 'playerId': 7}]}
        self.assertEqual(collect(self.state, after, self.old, self.new, 'now'), [])
        after['transactions']['t']['type'] = 'TRADE'
        self.assertEqual(collect(self.state, after, self.old, self.new, 'now')[0]['kind'], 'T')
        self.assertEqual(collect(after, after, self.old, self.new, 'later'), [])

    def test_waiver_add_and_drop_grouped_pending_and_lineup_excluded(self):
        after = deepcopy(self.state)
        after['transactions']['w'] = {'type': 'WAIVER', 'status': 'EXECUTED', 'items': [
            {'type': 'ADD', 'fromTeamId': 0, 'toTeamId': 1, 'playerId': 7},
            {'type': 'DROP', 'fromTeamId': 1, 'toTeamId': 0, 'playerId': 8}]}
        events = collect(self.state, after, self.old, self.new, 'now')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['teams'], [1])
        self.assertIn('off waivers', events[0]['text'])
        self.assertIn('dropped', events[0]['text'])
        after['transactions']['w']['status'] = 'PENDING'
        self.assertEqual(collect(self.state, after, self.old, self.new, 'now'), [])
        after['transactions']['w'] = {'type': 'ROSTER', 'status': 'EXECUTED',
            'items': [{'type': 'LINEUP', 'fromTeamId': 1, 'toTeamId': 1, 'playerId': 7}]}
        self.assertEqual(collect(self.state, after, self.old, self.new, 'now'), [])


if __name__ == '__main__':
    unittest.main()
