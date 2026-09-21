# London City Fantasy Football odds

A read-only fan page for our ESPN league: daily title and playoff odds, charted over time.

- `scripts/update.py` pulls the league from ESPN, builds each team's best projected lineup,
  blends it with real scores and simulates the rest of the season and playoffs 20,000 times.
- `.github/workflows/update.yml` runs it every day at 11:00 UTC and commits a new snapshot.
- `docs/` is the static site served by GitHub Pages (`docs/data/history.json` is the time series).

The league is private, so the Action needs two repo secrets from a logged-in ESPN browser session:
`ESPN_S2` and `SWID` (cookies on fantasy.espn.com). Alternatively the league manager can make the
league viewable to the public, and no secrets are needed.

Run locally: `python3 scripts/update.py` (with `ESPN_S2`/`SWID` exported).
