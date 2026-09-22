import unittest

from update import best_lineup


def player(name, position, season, pro_team):
    return [name, position, 20, season, {}, 0, pro_team]


class ByeLineupTests(unittest.TestCase):
    def test_bye_excludes_player_and_promotes_bench_replacement(self):
        players = [
            player('Starting QB', 1, 340, 10),
            player('Bench QB', 1, 255, 20),
            player('RB One', 2, 255, 30), player('RB Two', 2, 238, 31),
            player('WR One', 3, 272, 32), player('WR Two', 3, 255, 33),
            player('Flex WR', 3, 238, 34), player('TE', 4, 170, 35),
            player('K', 5, 153, 36), player('DST', 16, 170, 37),
        ]
        normal_total, normal = best_lineup(players)
        bye_total, bye = best_lineup(players, {10})
        self.assertIn(('Starting QB', 'QB', 20.0), normal)
        self.assertIn(('Bench QB', 'QB', 15.0), bye)
        self.assertNotIn(('Starting QB', 'QB', 20.0), bye)
        self.assertLess(bye_total, normal_total)

    def test_legacy_compact_player_without_pro_team_remains_available(self):
        legacy = ['Legacy QB', 1, 20, 340, {}, 0]
        total, lineup = best_lineup([legacy], {10})
        self.assertEqual(total, 20)
        self.assertEqual(lineup[0][0], 'Legacy QB')


if __name__ == '__main__':
    unittest.main()
