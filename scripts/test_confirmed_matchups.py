import unittest

from update import simulate


def team(team_id):
    return {
        "id": team_id, "name": f"Team {team_id}", "abbrev": f"T{team_id}",
        "logo": "", "players": [[f"QB {team_id}", 1, 0, 340, {}, 0, team_id]],
    }


class ConfirmedMatchupTests(unittest.TestCase):
    def test_current_week_winners_count_once_before_scoring_period_rolls(self):
        league = {
            "teams": [team(i) for i in range(1, 5)],
            "sched": [
                [3, 1, 2, 150.5, 99.25, "HOME", None],
                [3, 3, 4, 90.0, 120.0, "AWAY", None],
            ],
        }
        result = simulate(league, week=3, week_started=True, seed=7, n_sims=100)
        teams = {team["id"]: team for team in result["teams"]}

        self.assertEqual([(teams[i]["wins"], teams[i]["losses"]) for i in range(1, 5)],
                         [(1, 0), (0, 1), (0, 1), (1, 0)])
        self.assertEqual([teams[i]["projWins"] for i in range(1, 5)], [1, 0, 0, 1])
        self.assertEqual(teams[1]["pf"], 150.5)
        self.assertEqual(teams[2]["pf"], 99.25)
        self.assertEqual([m["homeWin"] for m in result["matchups"]], [1.0, 0.0])
        self.assertEqual(result["matchups"][0]["homePts"], 150.5)

    def test_unconfirmed_current_week_game_is_still_simulated(self):
        league = {
            "teams": [team(i) for i in range(1, 5)],
            "sched": [
                [3, 1, 2, 150.5, 99.25, "HOME", None],
                [3, 3, 4, 0, 0, "UNDECIDED", None],
            ],
        }
        result = simulate(league, week=3, week_started=False, seed=7, n_sims=100)
        teams = {team["id"]: team for team in result["teams"]}

        self.assertEqual((teams[1]["projWins"], teams[2]["projWins"]), (1, 0))
        self.assertAlmostEqual(teams[3]["projWins"] + teams[4]["projWins"], 1)
        self.assertEqual(result["matchups"][0]["homeWin"], 1.0)
        self.assertGreater(result["matchups"][1]["homeWin"], 0)
        self.assertLess(result["matchups"][1]["homeWin"], 1)


if __name__ == "__main__":
    unittest.main()
