"""Persist observed injuries and executed trades, never inferred news."""
import json
from prediction_events import diff_states


def collect(before, after, old_result, result, at):
    if not before:
        return []
    old = {t['id']: t for t in (old_result or {}).get('teams', [])}
    current = {t['id']: t for t in result['teams']}
    events = []
    def add(key, kind, ids, text):
        ids = sorted(i for i in set(ids) if i in current)
        if not ids:
            return
        changes = [{'team': i, 'before': old[i]['champ'], 'after': current[i]['champ']}
                   for i in ids if i in old]
        events.append({'id': key, 'kind': kind, 'at': at, 'teams': ids,
                       'text': text, 'changes': changes})
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
        ids, facts = [], []
        for item in tx.get('items', []):
            source, target = item.get('fromTeamId'), item.get('toTeamId')
            name = after.get('players', {}).get(str(item.get('playerId')), {}).get('name', 'a player')
            action = item.get('type')
            if action == 'ADD' and target in current:
                ids.append(target)
                verb = 'claimed' if tx.get('type') == 'WAIVER' else 'added'
                suffix = ' off waivers' if tx.get('type') == 'WAIVER' else ' from free agency'
                facts.append(f"{current[target]['name']} {verb} {name}{suffix}.")
            elif action == 'DROP' and source in current:
                ids.append(source)
                facts.append(f"{current[source]['name']} dropped {name}.")
            elif tx.get('type') == 'TRADE' and source in current and target in current and source != target:
                ids.extend([source, target])
                facts.append(f"{current[target]['name']} acquired {name} from {current[source]['name']}.")
        add(f'trade-{key}', 'T', ids, ' '.join(facts))
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
