# London City Fantasy Football odds

A read-only fan page for our ESPN league: daily title and playoff odds, charted over time.

- `scripts/update.py` pulls the league, player pool, and transaction feed from ESPN, builds each
  team's best projected lineup, blends it with real scores and simulates the rest of the season
  and playoffs 20,000 times.
- `.github/workflows/update.yml` runs hourly and commits a timestamped prediction snapshot.
  It detects roster moves, trades, player availability/projection changes (including relevant
  non-rostered-player news), and live scoring changes before explaining material odds moves.
- `python scripts/update.py --backfill` reconstructs hourly points from the post-draft seed through
  the present using ESPN's retained transaction timestamps and completed scores. Those points are
  labelled “Reconstructed”: ESPN does not publish an archive of historical hourly projections or
  injury cards, so the site does not present those inputs as observed facts.
- `docs/` is the static site served by GitHub Pages (`docs/data/history.json` is the time series).

The league is private, so the Action needs two repo secrets from a logged-in ESPN browser session:
`ESPN_S2` and `SWID` (cookies on fantasy.espn.com). Alternatively the league manager can make the
league viewable to the public, and no secrets are needed.

For natural-language graph tooltips, add `OPENROUTER_API_KEY` as a repository secret. The workflow
uses Jev (`typesafe/jev-1.13`) only to select a validated cause, then OpenAI GPT-5.6 Luna at low reasoning
effort (`openai/gpt-5.6-luna`) to write
one constrained sentence. Without that optional secret, the same deterministic event tree produces
a factual template tooltip instead.

Run locally: `python3 scripts/update.py` (with `ESPN_S2`/`SWID` exported).
