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
        if tx.get('type') != 'TRADE' or tx.get('status') != 'EXECUTED' or prior.get('status') == 'EXECUTED':
            continue
        ids, facts = [], []
        for item in tx.get('items', []):
            source, target = item.get('fromTeamId'), item.get('toTeamId')
            if source not in current or target not in current or source == target:
                continue
            ids.extend([source, target])
            name = after.get('players', {}).get(str(item.get('playerId')), {}).get('name', 'a player')
            facts.append(f"{current[target]['name']} acquired {name} from {current[source]['name']}.")
        add(f'trade-{key}', 'T', ids, ' '.join(facts))
    return events


def save_events(path, events):
    existing = json.loads(path.read_text()) if path.exists() else []
    known = {e['id']: e for e in existing}
    known.update({e['id']: e for e in events})
    path.write_text(json.dumps(list(known.values()), indent=1))
