#!/usr/bin/env python3
"""Fetch the ESPN league, simulate the rest of the season, and append a daily snapshot.

Usage:
  python scripts/update.py                   # live fetch (needs ESPN_S2 + SWID env if league is private)
  python scripts/update.py --backfill        # rebuild the backfilled history points from data/seed/
"""
import argparse, datetime as dt, json, math, os, random, sys, urllib.request
from pathlib import Path

from prediction_events import explain_prediction_moves, state_from_league

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

    ESPN's player endpoint pages at 500 records. Tracking this pool lets a
    relevant injury or projection re-rate outside a roster explain a later
    opportunity change for a rostered teammate.
    """
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
           f"/segments/0/leagues/{LEAGUE_ID}?view=kona_player_info")
    out, seen = [], set()
    for offset in range(0, 2500, 500):
        filter_ = {"players": {"limit": 500, "offset": offset,
                   "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]}}}
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                     "x-fantasy-filter": json.dumps(filter_, separators=(",", ":"))})
        if s2 and swid:
            req.add_header("Cookie", f"espn_s2={s2}; SWID={swid}")
        with urllib.request.urlopen(req, timeout=30) as response:
            batch = json.load(response).get("players") or []
        for entry in batch:
            ident = str(entry.get("id", entry.get("player", {}).get("id", "")))
            if ident and ident not in seen:
                out.append(entry); seen.add(ident)
        if len(batch) < 500:
            break
    return out


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


def simulate(league, week, week_started, seed):
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

    for _ in range(N_SIMS):
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
            "projWins": round(tot_wins[i] / N_SIMS, 2),
            "playoff": round(playoff[i] / N_SIMS, 4), "final": round(final_app[i] / N_SIMS, 4),
            "champ": round(champ[i] / N_SIMS, 4), "topSeed": round(seed1[i] / N_SIMS, 4),
            "lineup": [{"name": n, "pos": p, "pts": round(v, 1)} for n, p, v in lineups[i]],
        })
    out_teams.sort(key=lambda x: -x["champ"])

    matchups = []
    for k, (w, h, a, *_r) in enumerate(todo):
        if w != week:
            continue
        m = {"home": h, "away": a, "homeWin": round(this_week.get(k, 0) / N_SIMS, 4)}
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


def backfill():
    seed = json.loads((SEED_DIR / "2026-09-21.json").read_text())
    cur, wk1 = seed["current"], seed["week1"]
    history = load_history()
    undecided = [[*m[:3], 0, 0, "UNDECIDED", m[6]] for m in cur["sched"]]
    pre = {**wk1, "sched": undecided}
    record(history, "2026-09-09", "Preseason", simulate(pre, 1, False, 909), backfilled=True)
    record(history, "2026-09-15", "After week 1", simulate(cur, 2, False, 915), backfilled=True)
    result = simulate(cur, 2, week_started(cur, 2), 921)
    record(history, "2026-09-21", "Week 2 (live)", result)
    save(history, result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()
    if args.backfill:
        return backfill()
    raw_league = fetch_live()
    league = mini(raw_league)
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
    record(history, today, f"Week {week} · {now:%H}:00 UTC", result, at=at, changes=changes)
    save(history, result)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, separators=(",", ":")))
    print(f"{today}: week {week}; top odds:",
          ", ".join(f"{t['name']} {t['champ']:.0%}" for t in result["teams"][:3]))


if __name__ == "__main__":
    main()
