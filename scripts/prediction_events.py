"""Event diffing and explainers for the hourly ESPN prediction history.

The prediction model remains deterministic. Jev chooses from a fixed cause
taxonomy and a small prose model only turns verified facts into a tooltip.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

CAUSES = {
    "score_update", "trade", "roster_move", "roster_availability",
    "external_opportunity", "projection_rerating", "standings_context",
}
JEV_MODEL = os.getenv("OPENROUTER_MODEL_CLASSIFIER", "typesafe/jev-1.13")
EXPLAINER_MODEL = os.getenv("OPENROUTER_MODEL_EXPLAINER", "openai/gpt-4.1-nano")


def _stat_signal(player, week):
    actual = projection = season = None
    for stat in player.get("stats") or []:
        if stat.get("seasonId") != 2026:
            continue
        if stat.get("statSplitTypeId") == 1 and stat.get("scoringPeriodId") == week:
            if stat.get("statSourceId") == 0:
                actual = stat.get("appliedTotal")
            elif stat.get("statSourceId") == 1:
                projection = stat.get("appliedTotal")
        elif (stat.get("statSplitTypeId") == 0 and stat.get("statSourceId") == 1
              and stat.get("scoringPeriodId") == 0):
            season = stat.get("appliedTotal")
    return {"actual": actual, "projection": projection, "season": season}


def state_from_league(league, week):
    """Store only inputs relevant to explainable prediction changes."""
    players = {}

    def put(entry, team_id=None, slot=None):
        player = entry.get("player", entry)
        ident = str(player.get("id", entry.get("id", "")))
        if not ident:
            return
        players[ident] = {
            "id": ident,
            "name": player.get("fullName", "Unknown player"),
            "team": team_id if team_id is not None else entry.get("onTeamId"),
            "slot": slot if slot is not None else entry.get("lineupSlotId"),
            "pro": player.get("proTeamId"),
            "position": player.get("defaultPositionId"),
            "injury": player.get("injuryStatus", "ACTIVE"),
            "active": player.get("active", True),
            "stats": _stat_signal(player, week),
        }

    for entry in league.get("players") or []:
        put(entry)
    for team in league.get("teams") or []:
        for roster in team.get("roster", {}).get("entries", []):
            put(roster.get("playerPoolEntry", {}), team["id"], roster.get("lineupSlotId"))

    transactions = {}
    for transaction in league.get("transactions") or []:
        ident = str(transaction.get("id", ""))
        if not ident:
            continue
        transactions[ident] = {
            "id": ident, "type": transaction.get("type"), "team": transaction.get("teamId"),
            "member": transaction.get("memberId"), "status": transaction.get("status"),
            "at": transaction.get("processDate") or transaction.get("proposedDate"),
            "items": [{k: item.get(k) for k in ("playerId", "fromTeamId", "toTeamId", "type")}
                      for item in transaction.get("items") or []],
        }
    return {"week": week, "players": players, "transactions": transactions}


def _changed(a, b):
    return a != b and not (a is None and b is None)


def diff_states(before, after):
    """Return factual, compact events. No inferred cause is introduced here."""
    if not before:
        return []
    events = []
    old_players, new_players = before.get("players", {}), after.get("players", {})
    for ident, now in new_players.items():
        old = old_players.get(ident)
        if not old:
            continue
        if old.get("team") != now.get("team") or old.get("slot") != now.get("slot"):
            events.append({"kind": "roster_move", "player": now["name"], "playerId": ident,
                           "from": old.get("team"), "to": now.get("team"), "pro": now.get("pro")})
        if _changed(old.get("injury"), now.get("injury")):
            events.append({"kind": "injury", "player": now["name"], "playerId": ident,
                           "team": now.get("team"), "pro": now.get("pro"),
                           "from": old.get("injury"), "to": now.get("injury")})
        for field in ("projection", "actual"):
            was, is_now = old["stats"].get(field), now["stats"].get(field)
            if _changed(was, is_now):
                events.append({"kind": field, "player": now["name"], "playerId": ident,
                               "team": now.get("team"), "pro": now.get("pro"),
                               "from": was, "to": is_now})
    for ident, transaction in after.get("transactions", {}).items():
        if ident not in before.get("transactions", {}):
            events.append({"kind": "transaction", **transaction})
    return events


def _team_events(team_id, events, state):
    roster_pros = {p.get("pro") for p in state.get("players", {}).values() if p.get("team") == team_id}
    relevant = []
    for event in events:
        direct = team_id in (event.get("team"), event.get("from"), event.get("to"))
        if event.get("kind") == "transaction":
            direct = direct or any(team_id in (item.get("fromTeamId"), item.get("toTeamId"))
                                for item in event.get("items", []))
        external = event.get("kind") == "injury" and event.get("pro") in roster_pros and not direct
        if direct or external:
            relevant.append({**event, "external": external})
    return relevant


def deterministic_cause(events):
    if any(e.get("kind") == "actual" for e in events):
        return "score_update"
    if any(e.get("kind") == "transaction" and e.get("type") == "TRADE" for e in events):
        return "trade"
    if any(e.get("kind") == "transaction" and e.get("type", "").startswith("TRADE") for e in events):
        return "trade"
    if any(e.get("kind") == "roster_move" for e in events):
        return "roster_move"
    if any(e.get("kind") == "injury" and not e.get("external") for e in events):
        return "roster_availability"
    if any(e.get("external") for e in events):
        return "external_opportunity"
    if any(e.get("kind") == "projection" for e in events):
        return "projection_rerating"
    return "standings_context"


def _chat(model, messages, max_tokens):
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/George5562/london-city-ff",
                 "X-Title": "London City Fantasy Football"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _fact_lines(events):
    lines = []
    for event in events[:8]:
        if event["kind"] == "transaction":
            lines.append(f"transaction {event.get('type')} involving team {event.get('team')}")
        elif event["kind"] in ("projection", "actual", "injury", "roster_move"):
            lines.append(f"{event['kind']}: {event['player']} {event.get('from')} -> {event.get('to')}")
    return lines


def jev_cause(fallback, events):
    """Use Jev as a constrained reviewer; the deterministic result is safe fallback."""
    facts = _fact_lines(events)
    if not facts:
        return fallback, "deterministic"
    raw = _chat(JEV_MODEL, [
        {"role": "system", "content": "Classify verified fantasy-football facts. Return only JSON with a cause."},
        {"role": "user", "content": json.dumps({"allowed": sorted(CAUSES), "facts": facts, "fallback": fallback})},
    ], 24)
    try:
        choice = json.loads(raw or "{}").get("cause")
    except json.JSONDecodeError:
        choice = None
    return (choice, "jev") if choice in CAUSES else (fallback, "deterministic")


def template(cause, events, delta):
    fact = _fact_lines(events)
    subject = fact[0] if fact else "updated league standings and projections"
    direction = "rose" if delta > 0 else "fell"
    return f"Title odds {direction} {abs(delta) * 100:.1f} points after {subject}."


def explain(cause, events, delta):
    fallback = template(cause, events, delta)
    raw = _chat(EXPLAINER_MODEL, [
        {"role": "system", "content": "Write one factual tooltip sentence, 28 words maximum. Do not invent facts or claims."},
        {"role": "user", "content": json.dumps({"cause": cause, "title_odds_delta_points": round(delta * 100, 2),
             "facts": _fact_lines(events), "fallback": fallback})},
    ], 60)
    if not raw or len(raw.split()) > 32:
        return fallback, "template"
    return raw.replace("\n", " "), "model"


def explain_prediction_moves(before_result, after_result, before_state, after_state):
    """Return one tooltip payload per material team-title-odds move."""
    if not before_result or not before_state:
        return {}
    events = diff_states(before_state, after_state)
    old = {team["id"]: team for team in before_result.get("teams", [])}
    explainers = {}
    for team in after_result.get("teams", []):
        previous = old.get(team["id"])
        if not previous:
            continue
        delta = team["champ"] - previous["champ"]
        # A half percentage point is visually meaningful at the chart's precision.
        if abs(delta) < 0.005:
            continue
        relevant = _team_events(team["id"], events, after_state)
        cause, classified_by = jev_cause(deterministic_cause(relevant), relevant)
        sentence, written_by = explain(cause, relevant, delta)
        explainers[str(team["id"])] = {"cause": cause, "classifiedBy": classified_by,
            "writtenBy": written_by, "delta": round(delta, 4), "text": sentence,
            "facts": _fact_lines(relevant)}
    return explainers
