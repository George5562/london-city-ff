#!/usr/bin/env python3
"""Fetch the ESPN league, simulate the rest of the season, and append a daily snapshot.

Usage:
  python scripts/update.py                   # live fetch (needs ESPN_S2 + SWID env if league is private)
  python scripts/update.py --backfill        # rebuild the backfilled history points from data/seed/
"""
import argparse, copy, datetime as dt, gzip, json, math, os, random, sys, urllib.request
from pathlib import Path

from prediction_events import explain_prediction_moves, state_from_league
from audit_history import audit

LEAGUE_ID = 227341815
SEASON = 2026
ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "docs" / "data"
SEED_DIR = ROOT / "data" / "seed"
STATE_FILE = ROOT / "data" / "state" / "league-state.json"

N_SIMS = 20000
WEEKLY_SD = 26.0        # game-to-game noise in a team's weekly score
TALENT_SD = 7.0         # per-simulation uncertainty in a team's true strength
PRIOR_GAMES = 8         # how many games of projection the actual scoring average is weighed against
BENCH, IR = 20, 21
# lineup: QB, RB x2, WR x2, RB/WR flex, TE, D/ST, K
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
AVAIL = {"INJURY_RESERVE": 0.35, "OUT": 0.6, "DOUBTFUL": 0.85, "SUSPENSION": 0.5}


# ---------------------------------------------------------------- fetch
def fetch_live():
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
           f"/segments/0/leagues/{LEAGUE_ID}?view=mTeam&view=mMatchupScore&view=mRoster&view=mSettings&view=mTransactions2")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    s2, swid = os.environ.get("ESPN_S2"), os.environ.get("SWID")
    if s2 and swid:
        req.add_header("Cookie", f"espn_s2={s2}; SWID={swid}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            league = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("ESPN returned 401: league is private. Set ESPN_S2 and SWID secrets, "
                     "or make the league viewable to the public.")
        raise
    league["players"] = fetch_player_pool(s2, swid)
    return league


def fetch_player_pool(s2, swid):
    """Fetch the full player pool, not only the league's rostered players.

    ESPN requires a sort whenever a player limit is requested. A single sorted
    3,000-player response covers the active NFL pool and avoids silently
    watching only the endpoint's default first 50 players.
    """
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
           f"/segments/0/leagues/{LEAGUE_ID}?view=kona_player_info")
    filter_ = {"players": {"limit": 3000,
               "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                 "x-fantasy-filter": json.dumps(filter_, separators=(",", ":"))})
    if s2 and swid:
        req.add_header("Cookie", f"espn_s2={s2}; SWID={swid}")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response).get("players") or []


def mini(j):
    """Shrink a raw ESPN league response to the compact shape the model uses (same as the seed files)."""
    def player(e):
        p = e["playerPoolEntry"]["player"]
        stats = [s for s in p.get("stats") or [] if s.get("seasonId") == SEASON]
        season = next((round(s["appliedTotal"], 1) for s in stats
                       if s["statSourceId"] == 1 and s["statSplitTypeId"] == 0 and s["scoringPeriodId"] == 0), None)
        wk = {}
        for s in stats:
            if s["statSplitTypeId"] == 1:
                wk.setdefault(str(s["scoringPeriodId"]), [None, None])[s["statSourceId"]] = round(s["appliedTotal"], 1)
        inj = p.get("injuryStatus")
        return [p["fullName"], p["defaultPositionId"], e["lineupSlotId"], season, wk, 0 if inj == "ACTIVE" else inj]

    sched = [[m["matchupPeriodId"], m.get("home", {}).get("teamId"), m.get("away", {}).get("teamId"),
              m.get("home", {}).get("totalPoints"), m.get("away", {}).get("totalPoints"),
              m.get("winner"), m.get("playoffTierType")] for m in j.get("schedule", [])]
    teams = [{"id": t["id"], "name": t["name"], "abbrev": t["abbrev"], "logo": t.get("logo"),
              "players": [player(e) for e in t.get("roster", {}).get("entries", [])]} for t in j["teams"]]
    return {"week": j["scoringPeriodId"], "sched": sched, "teams": teams}


# ---------------------------------------------------------------- model
def per_game(p):
    name, pos, slot, season, wk, inj = p
    return (season or 0) / 17.0 * AVAIL.get(inj or "", 1.0)


def best_lineup(players):
    """Greedy optimal lineup by per-game projection. Returns (total, [(name, pos, pts)])."""
    pool = sorted(((per_game(p), p) for p in players), key=lambda x: -x[0])
    need = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1}
    picked, used = [], set()
    for v, p in pool:
        pos = POS.get(p[1])
        if pos and need.get(pos, 0) > 0:
            need[pos] -= 1; picked.append((p[0], pos, v)); used.add(p[0])
    for v, p in pool:  # flex
        if p[0] not in used and POS.get(p[1]) in ("RB", "WR"):
            picked.append((p[0], "FLEX", v)); break
    return sum(x[2] for x in picked), picked


def live_week(team, week):
    """Points banked this week by starters, plus projection for starters yet to play."""
    banked = remaining = 0.0
    for name, pos, slot, season, wk, inj in team["players"]:
        if slot in (BENCH, IR):
            continue
        act, proj = (wk.get(str(week)) or [None, None])
        if act is not None:
            banked += act
        else:
            remaining += proj if proj is not None else per_game([name, pos, slot, season, wk, inj])
    return banked, remaining


def weekly_level(league, week):
    """Average ESPN weekly projection for each team's best lineup this week."""
    totals = []
    for t in league["teams"]:
        ps = [[p[0], p[1], p[2], ((p[4].get(str(week)) or [None, None])[1] or 0) * 17, p[4], p[5]]
              for p in t["players"]]
        totals.append(best_lineup(ps)[0])
    return sum(totals) / len(totals) if any(totals) else 120.0


def simulate(league, week, week_started, seed, n_sims=N_SIMS):
    teams = {t["id"]: t for t in league["teams"]}
    sched = league["sched"]
    reg_weeks = max(m[0] for m in sched)
    final = [m for m in sched if m[5] in ("HOME", "AWAY", "TIE") and m[0] < week]

    wins = {i: 0.0 for i in teams}; pf = {i: 0.0 for i in teams}; games = {i: 0 for i in teams}
    for w, h, a, hs, as_, win, _ in final:
        pf[h] += hs; pf[a] += as_; games[h] += 1; games[a] += 1
        wins[h] += 1 if win == "HOME" else 0.5 if win == "TIE" else 0
        wins[a] += 1 if win == "AWAY" else 0.5 if win == "TIE" else 0

    proj, lineups, mu = {}, {}, {}
    for i, t in teams.items():
        proj[i], lineups[i] = best_lineup(t["players"])
    # Season projections run low vs what this league actually scores; rescale them to the
    # league's scoring level (ESPN's weekly projections, then real results as they come in).
    mean_proj = sum(proj.values()) / len(proj)
    level = weekly_level(league, week)
    n_games = sum(games.values())
    target = (PRIOR_GAMES * len(teams) * level + sum(pf.values())) / (PRIOR_GAMES * len(teams) + n_games)
    scale = target / mean_proj
    for i in teams:
        proj[i] *= scale
        lineups[i] = [(n, p, v * scale) for n, p, v in lineups[i]]
        mu[i] = (PRIOR_GAMES * proj[i] + pf[i]) / (PRIOR_GAMES + games[i])

    live = {}
    if week_started:
        for i, t in teams.items():
            live[i] = live_week(t, week)

    todo = [m for m in sched if m[0] >= week]
    rng = random.Random(seed)
    champ = {i: 0 for i in teams}; playoff = {i: 0 for i in teams}; final_app = {i: 0 for i in teams}
    tot_wins = {i: 0.0 for i in teams}; seed1 = {i: 0 for i in teams}
    this_week = {}  # matchup index -> home wins
    ids = list(teams)

    def score(i, talent, w):
        if w == week and week_started:
            banked, rem = live[i]
            frac = rem / max(proj[i], 1)
            return banked + rem * (talent[i] / max(mu[i], 1)) + rng.gauss(0, WEEKLY_SD * math.sqrt(min(frac, 1)))
        return rng.gauss(talent[i], WEEKLY_SD)

    for _ in range(n_sims):
        talent = {i: mu[i] + rng.gauss(0, TALENT_SD) for i in ids}
        w_ = dict(wins); p_ = dict(pf)
        for k, (w, h, a, *_r) in enumerate(todo):
            sh, sa = score(h, talent, w), score(a, talent, w)
            p_[h] += sh; p_[a] += sa
            if sh > sa: w_[h] += 1
            else: w_[a] += 1
            if w == week:
                this_week[k] = this_week.get(k, 0) + (sh > sa)
        order = sorted(ids, key=lambda i: (w_[i], p_[i]), reverse=True)
        seed1[order[0]] += 1
        for i in ids:
            tot_wins[i] += w_[i]
        top = order[:4]
        for i in top:
            playoff[i] += 1
        def game(x, y):
            return x if rng.gauss(talent[x], WEEKLY_SD) > rng.gauss(talent[y], WEEKLY_SD) else y
        f1, f2 = game(top[0], top[3]), game(top[1], top[2])
        final_app[f1] += 1; final_app[f2] += 1
        champ[game(f1, f2)] += 1

    out_teams = []
    for i, t in teams.items():
        rec_w = sum(1 for m in final if (m[1] == i and m[5] == "HOME") or (m[2] == i and m[5] == "AWAY"))
        rec_l = sum(1 for m in final if (m[1] == i and m[5] == "AWAY") or (m[2] == i and m[5] == "HOME"))
        out_teams.append({
            "id": i, "name": t["name"], "abbrev": t["abbrev"], "logo": t["logo"],
            "wins": rec_w, "losses": rec_l, "pf": round(pf[i], 2),
            "projPerGame": round(proj[i], 1), "strength": round(mu[i], 1),
            "projWins": round(tot_wins[i] / n_sims, 2),
            "playoff": round(playoff[i] / n_sims, 4), "final": round(final_app[i] / n_sims, 4),
            "champ": round(champ[i] / n_sims, 4), "topSeed": round(seed1[i] / n_sims, 4),
            "lineup": [{"name": n, "pos": p, "pts": round(v, 1)} for n, p, v in lineups[i]],
        })
    out_teams.sort(key=lambda x: -x["champ"])

    matchups = []
    for k, (w, h, a, *_r) in enumerate(todo):
        if w != week:
            continue
        m = {"home": h, "away": a, "homeWin": round(this_week.get(k, 0) / n_sims, 4)}
        if week_started:
            m.update(homePts=round(live[h][0], 2), awayPts=round(live[a][0], 2),
                     homeProj=round(live[h][0] + live[h][1], 1), awayProj=round(live[a][0] + live[a][1], 1))
        matchups.append(m)

    return {"week": week, "regularSeasonWeeks": reg_weeks, "teams": out_teams, "matchups": matchups}


# ---------------------------------------------------------------- history
def load_history():
    f = SITE_DATA / "history.json"
    return json.loads(f.read_text()) if f.exists() else {"snapshots": []}


def record(history, date, label, result, backfilled=False, at=None, changes=None):
    snap = {"date": date, "label": label, "week": result["week"], "backfilled": backfilled,
            "teams": {str(t["id"]): {"champ": t["champ"], "playoff": t["playoff"], "projWins": t["projWins"]}
                      for t in result["teams"]}}
    if at:
        snap["at"] = at
    if changes:
        snap["changes"] = changes
    key = at or date
    history["snapshots"] = [s for s in history["snapshots"] if s.get("at", s["date"]) != key] + [snap]
    history["snapshots"].sort(key=lambda s: s.get("at", s["date"]))


def save(history, result):
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "history.json").write_text(json.dumps(history, indent=1))
    result["generated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    (SITE_DATA / "latest.json").write_text(json.dumps(result, indent=1))


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def week_started(league, week):
    """True once any rostered player has an actual score for this week."""
    return any((p[4].get(str(week)) or [None])[0] is not None
               for t in league["teams"] for p in t["players"] if p[2] not in (BENCH, IR))


POST_DRAFT_AT = dt.datetime(2026, 9, 9, 18, tzinfo=dt.timezone.utc)
WEEK_ONE_CLOSE_AT = dt.datetime(2026, 9, 15, 6, tzinfo=dt.timezone.utc)
SCOREBOARD_DATES = ("20260910", "20260911", "20260913", "20260914", "20260915",
                    "20260918", "20260920", "20260921")


def _hour(value):
    return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).replace(minute=0, second=0, microsecond=0)


def _nfl_game_ends():
    """Return final NFL games grouped by a conservative game-end hour.

    The public scoreboard supplies completed-game facts; using kickoff plus four
    hours avoids claiming a score before the game could have finished.
    """
    games = []
    for date in SCOREBOARD_DATES:
        url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=" + date + "&limit=100"
        try:
            request = urllib.request.Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "curl/8.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                if body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                board = json.loads(body)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
        week = (board.get("week") or {}).get("number")
        for event in board.get("events") or []:
            if event.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL" or not week:
                continue
            kickoff = dt.datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            clubs = {int(c["team"]["id"]) for c in event.get("competitions", [{}])[0].get("competitors", [])}
            games.append((int(week), (kickoff + dt.timedelta(hours=4)).replace(minute=0, second=0, microsecond=0), clubs))
    return games


def _compact_player(state_player, week):
    """Create the compact simulator shape for a player added after the draft.

    ESPN retains the activity timestamp and current player card, but not a historical
    projection-card archive. This deliberately uses the current card and marks the
    resulting snapshots as reconstructed.
    """
    stats = state_player.get("stats") or {}
    injury = state_player.get("injury") or "ACTIVE"
    return [state_player.get("name", f"Player {state_player['id']}"), state_player.get("position"), BENCH,
            stats.get("season"), {str(week): [None, stats.get("projection")]},
            0 if injury == "ACTIVE" else injury]


def _transaction_text(event, players, teams):
    parts = []
    for item in event.get("items") or []:
        player = players.get(str(item.get("playerId")), {})
        name = player.get("name", f"player {item.get('playerId')}")
        action = item.get("type", "move").lower()
        if action == "lineup":
            parts.append(f"{name}'s lineup was changed")
        elif action == "add":
            parts.append(f"{name} was added")
        elif action == "drop":
            parts.append(f"{name} was dropped")
        else:
            parts.append(f"{name} moved")
    actor = teams.get(event.get("team"), "A league team")
    return f"Historical ESPN activity: {actor} " + (", ".join(parts) if parts else event.get("type", "activity").lower()) + "."


def _affected_teams(event):
    affected = {event.get("team")} if event.get("team") else set()
    for item in event.get("items") or []:
        affected.update(x for x in (item.get("fromTeamId"), item.get("toTeamId")) if x)
    return affected


def _apply_transaction(league, event, players, week):
    """Apply roster ownership changes; ESPN does not retain historical lineup slots."""
    rosters = {team["id"]: team["players"] for team in league["teams"]}
    for item in event.get("items") or []:
        player_id = str(item.get("playerId"))
        action, from_team, to_team = item.get("type"), item.get("fromTeamId"), item.get("toTeamId")
        if action == "DROP" and from_team in rosters:
            rosters[from_team][:] = [p for p in rosters[from_team] if p[0] != players.get(player_id, {}).get("name")]
        elif action == "ADD" and to_team in rosters and player_id in players:
            name = players[player_id].get("name")
            if not any(p[0] == name for p in rosters[to_team]):
                rosters[to_team].append(_compact_player(players[player_id], week))
        elif action not in ("LINEUP", "ADD", "DROP") and from_team in rosters and to_team in rosters:
            moving = [p for p in rosters[from_team] if p[0] == players.get(player_id, {}).get("name")]
            rosters[from_team][:] = [p for p in rosters[from_team] if p not in moving]
            rosters[to_team].extend(moving)


def _apply_final_scores(league, week, clubs, player_state, current_scores):
    """Reveal already-recorded ESPN fantasy points once the real NFL game ended."""
    changed = []
    pro_by_name = {p.get("name"): p.get("pro") for p in player_state.values()}
    for team in league["teams"]:
        for player in team["players"]:
            if pro_by_name.get(player[0]) not in clubs:
                continue
            actual, projection = current_scores.get((week, player[0]), (None, None))
            if actual is None:
                continue
            line = player[4].setdefault(str(week), [None, projection])
            if line[0] != actual:
                line[0] = actual
                changed.append((team["id"], player[0], actual))
    return changed


def backfill():
    """Reconstruct hourly points from the retained ESPN transaction timeline.

    This is intentionally a reconstruction: roster activity and final scores are
    historical facts, whereas ESPN does not expose archived hourly projections or
    injury cards. The graph labels all such points accordingly.
    """
    seed = json.loads((SEED_DIR / "2026-09-21.json").read_text())
    current, initial = seed["current"], seed["week1"]
    state = load_json(STATE_FILE)
    if not state:
        sys.exit("Cannot backfill without data/state/league-state.json. Run a live update first.")
    players = state.get("players", {})
    teams = {team["id"]: team["name"] for team in current["teams"]}
    current_scores = {}
    for source, week in ((initial, 1), (current, 2)):
        for team in source["teams"]:
            for player in team["players"]:
                values = player[4].get(str(week), [None, None])
                current_scores[(week, player[0])] = (values[0], values[1])
    events = sorted((event for event in state.get("transactions", {}).values()
                     if event.get("status") == "EXECUTED" and event.get("at")
                     and event.get("type") != "TRADE_DECLINE"), key=lambda event: event["at"])
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    history = load_history()
    # Preserve every live point; replace only our labelled historical reconstruction.
    history["snapshots"] = [snapshot for snapshot in history["snapshots"] if not snapshot.get("backfilled")]

    league = copy.deepcopy(initial)
    # The Week 1 seed intentionally contains no completed scores. Keep the known
    # schedule but remove outcomes for the post-draft forecast.
    league["sched"] = [[*match[:3], 0, 0, "UNDECIDED", match[6]] for match in current["sched"]]
    phase_week, phase_started = 1, False
    by_hour = {}
    for event in events:
        at = _hour(event["at"])
        if POST_DRAFT_AT <= at <= now:
            by_hour.setdefault(at, []).append(event)
    games_by_hour = {}
    for week, at, clubs in _nfl_game_ends():
        if POST_DRAFT_AT <= at <= now:
            games_by_hour.setdefault(at, []).append((week, clubs))
    # Do not let final scores leak into the pre-game historical forecast.
    for team in league["teams"]:
        for player in team["players"]:
            if "1" in player[4]:
                player[4]["1"][0] = None

    cursor = POST_DRAFT_AT
    cached_result = None
    while cursor <= now:
        phase_changed = False
        if cursor >= WEEK_ONE_CLOSE_AT and phase_week == 1:
            league["sched"] = copy.deepcopy(current["sched"])
            league["week"] = 2
            phase_week, phase_started = 2, False
            phase_changed = True
        hourly_events = by_hour.get(cursor, [])
        for event in hourly_events:
            _apply_transaction(league, event, players, phase_week)
        score_changes = []
        for score_week, clubs in games_by_hour.get(cursor, []):
            if score_week == phase_week:
                score_changes.extend(_apply_final_scores(league, score_week, clubs, players, current_scores))
        # The state is constant between retained activity timestamps, so reuse the
        # exact result rather than needlessly re-running 20,000 simulations/hour.
        if cached_result is None or phase_changed or hourly_events or score_changes:
            cached_result = simulate(league, phase_week, phase_started, int(cursor.strftime("%Y%m%d%H")), n_sims=1000)
        changes = {}
        for event in hourly_events:
            text = _transaction_text(event, players, teams)
            for team_id in _affected_teams(event):
                changes[str(team_id)] = {"cause": "roster_move", "text": text}
        for team_id, name, score in score_changes:
            changes[str(team_id)] = {"cause": "score_update",
                                     "text": f"Historical final score: {name} recorded {score:.1f} fantasy points."}
        label = "Post-draft reconstruction" if cursor == POST_DRAFT_AT else f"Reconstructed · {cursor:%d %b %H}:00 UTC"
        record(history, cursor.date().isoformat(), label, cached_result, backfilled=True,
               at=cursor.isoformat().replace("+00:00", "Z"), changes=changes)
        cursor += dt.timedelta(hours=1)

    latest = simulate(current, current["week"], week_started(current, current["week"]), int(now.strftime("%Y%m%d")))
    save(history, latest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()
    if args.backfill:
        return backfill()
    raw_league = fetch_live()
    league = mini(raw_league)
    from results import save_results
    save_results(league['sched'], dt.datetime.now(dt.timezone.utc).isoformat(timespec='minutes'))
    week = league["week"]
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    today = now.date().isoformat()
    result = simulate(league, week, week_started(league, week), int(today.replace("-", "")))
    history = load_history()
    previous_result = load_json(SITE_DATA / "latest.json")
    previous_state = load_json(STATE_FILE)
    state = state_from_league(raw_league, week)
    changes = explain_prediction_moves(previous_result, result, previous_state, state)
    at = now.isoformat().replace("+00:00", "Z")
    from timeline_events import collect, save_events
    save_events(SITE_DATA / 'events.json', collect(previous_state, state, previous_result, result, at))
    record(history, today, f"Week {week} · {now:%H}:00 UTC", result, at=at, changes=changes)
    history = audit(history, state, load_json(SEED_DIR / "2026-09-21.json"))
    save(history, result)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, separators=(",", ":")))
    print(f"{today}: week {week}; top odds:",
          ", ".join(f"{t['name']} {t['champ']:.0%}" for t in result["teams"][:3]))


if __name__ == "__main__":
    main()
