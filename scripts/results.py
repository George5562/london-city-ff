"""Keep final matchup scores and the first update that confirmed them."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def save_results(schedule, at, historical=False):
    path = ROOT / 'docs/data/results.json'
    records = json.loads(path.read_text()) if path.exists() else []
    known = {r['id']: r for r in records}
    for week, home, away, hp, ap, winner, *_ in schedule:
        if winner not in ('HOME', 'AWAY', 'TIE') or not home or not away:
            continue
        key = f'{week}-{home}-{away}'
        prior = known.get(key)
        known[key] = {'id': key, 'week': week, 'home': home, 'away': away,
                      'homePts': hp, 'awayPts': ap, 'winner': winner,
                      'at': prior['at'] if prior else at,
                      'historical': prior['historical'] if prior else historical}
    path.write_text(json.dumps(list(known.values()), indent=1))


if __name__ == '__main__':
    seed = json.loads((ROOT / 'data/seed/2026-09-21.json').read_text())
    save_results([m for m in seed['current']['sched'] if m[0] == 1],
                 '2026-09-15T06:00:00Z', historical=True)
