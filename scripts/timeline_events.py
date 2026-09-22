"""Persist observed injuries and executed trades, never inferred news."""
import json
import time
from prediction_events import EXPLAINER_MODEL, EXPLAINER_REASONING, _chat, diff_states

POSITIONS = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'D/ST'}
EVENT_COPY_MODEL = __import__('os').environ.get('OPENROUTER_MODEL_EVENT_COPY', EXPLAINER_MODEL)


def event_copy(moves):
    """A tiny model turns only retained transaction facts into spectator copy."""
    fallback = '; '.join(
        (f"Claimed {m['player']} off waivers" if m['action'] == 'claim' else
         f"Added {m['player']}" if m['action'] == 'add' else
         f"Dropped {m['player']}" if m['action'] == 'drop' else
         f"Traded for {m['player']}") for m in moves) + '.'
    messages = [
        {'role': 'system', 'content': 'Write one concise fantasy-football transaction tooltip, 24 words maximum. Use only the supplied facts. Do not name a fantasy team, speculate about impact, or mention models.'},
        {'role': 'user', 'content': json.dumps({'moves': moves, 'fallback': fallback})},
    ]
    # Luna can occasionally return a reasoning-only response with no display
    # content. Retry once without a reasoning budget before retaining factual
    # fallback copy; this one-off rebuild is deliberately bounded per event.
    for reasoning in (EXPLAINER_REASONING, None):
        # Leave enough room for Luna's low-reasoning trace *and* the requested
        # 24-word display sentence.  The visible result remains capped below.
        raw = _chat(EVENT_COPY_MODEL, messages, 180, reasoning)
        if raw and len(raw.split()) <= 28:
            time.sleep(.35)
            return raw.replace('\n', ' ')
        time.sleep(.4)
    return fallback


def collect(before, after, old_result, result, at):
    if not before:
        return []
    old = {t['id']: t for t in (old_result or {}).get('teams', [])}
    current = {t['id']: t for t in result['teams']}
    events = []
    def add(key, kind, ids, text, moves=None):
        ids = sorted(i for i in set(ids) if i in current)
        if not ids:
            return
        changes = [{'team': i, 'before': old[i]['champ'], 'after': current[i]['champ']}
                   for i in ids if i in old]
        events.append({'id': key, 'kind': kind, 'at': at, 'teams': ids,
                       'text': text, 'moves': moves or [], 'changes': changes})
    for event in diff_states(before, after):
        if event['kind'] == 'injury':
            team = event.get('team')
            if team not in current:
                continue
            status = str(event['to']).replace('_', ' ').lower()
            add(f"injury-{event['playerId']}-{at}", 'I', [team],
                f"{current[team]['name']}: {event['player']} is now listed as {status}.")
    for key, tx in after.get('transactions', {}).items():
        prior = before.get('transactions', {}).get(key, {})
        # Declined/proposed trades are not roster changes.
        if tx.get('type') not in ('TRADE', 'WAIVER', 'FREEAGENT', 'ROSTER') or tx.get('status') != 'EXECUTED' or prior.get('status') == 'EXECUTED':
            continue
        ids, moves = [], []
        for item in tx.get('items', []):
            source, target = item.get('fromTeamId'), item.get('toTeamId')
            player = after.get('players', {}).get(str(item.get('playerId')), {})
            name = player.get('name', 'a player')
            move = {'player': name, 'position': POSITIONS.get(player.get('position'), 'FLEX')}
            action = item.get('type')
            if action == 'ADD' and target in current:
                ids.append(target)
                verb = 'claimed' if tx.get('type') == 'WAIVER' else 'added'
                suffix = ' off waivers' if tx.get('type') == 'WAIVER' else ' from free agency'
                moves.append({**move, 'action': 'claim' if tx.get('type') == 'WAIVER' else 'add'})
            elif action == 'DROP' and source in current:
                ids.append(source)
                moves.append({**move, 'action': 'drop'})
            elif tx.get('type') == 'TRADE' and source in current and target in current and source != target:
                ids.extend([source, target])
                moves.append({**move, 'action': 'trade'})
        if moves:
            add(f'trade-{key}', 'T', ids, event_copy(moves), moves)
    return events


def save_events(path, events):
    existing = json.loads(path.read_text()) if path.exists() else []
    known = {e['id']: e for e in existing}
    known.update({e['id']: e for e in events})
    path.write_text(json.dumps(list(known.values()), indent=1))


if __name__ == '__main__':
    from pathlib import Path
    from datetime import datetime, timezone
    root = Path(__file__).resolve().parent.parent
    state = json.loads((root / 'data/state/league-state.json').read_text())
    latest = json.loads((root / 'docs/data/latest.json').read_text())
    history = json.loads((root / 'docs/data/history.json').read_text())['snapshots']
    def stamp(sn):
        return datetime.fromisoformat(sn.get('at', sn['date']+'T12:00:00Z').replace('Z', '+00:00')).timestamp()
    history.sort(key=stamp)
    backfilled = []
    for key, tx in state.get('transactions', {}).items():
        if not tx.get('at'):
            continue
        timestamp = tx['at']/1000
        at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        prior = next((s for s in reversed(history) if stamp(s) < timestamp), None)
        following = next((s for s in history if stamp(s) >= timestamp), None)
        def result(sn):
            return {'teams': [{**t, **sn['teams'].get(str(t['id']), {})} for t in latest['teams']]}
        # No historical injury times are inferred from current injury flags.
        before = {**state, 'transactions': {}}
        after = {**state, 'transactions': {key: tx}}
        events = collect(before, after, result(prior) if prior else None,
                         result(following) if following else latest, at)
        if not following:
            for event in events:
                event['changes'] = []
        backfilled.extend(events)
    save_events(root / 'docs/data/events.json', backfilled)
    print(f'Retained {len(backfilled)} confirmed roster transactions')
