"""Explain material stored movements without inventing historical causality.

Legacy backfill ran with phase_started=False and a new random seed per event.
Its score-only/lineup-only updates therefore changed sampling, not model inputs.
Keep the original snapshots and evidence, but expose this distinction on hover.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def timestamp(snapshot):
    return datetime.fromisoformat(snapshot.get('at', snapshot['date'] + 'T12:00:00Z').replace('Z', '+00:00'))


def audit(history, state, seed):
    snapshots = sorted(history['snapshots'], key=timestamp)
    names = {team['id']: team['name'] for team in seed['current']['teams']}
    transactions = list(state.get('transactions', {}).values())
    for previous, current in zip(snapshots, snapshots[1:]):
        # Preserve previously audited history when the live transaction window rolls on.
        if current.get('explanations') and current.get('backfilled'):
            continue
        current['explanations'] = {}
        hour = timestamp(current)
        events = [event for event in transactions
                  if event.get('status') == 'EXECUTED' and event.get('at')
                  and datetime.fromtimestamp(event['at']/1000, timezone.utc).replace(minute=0, second=0, microsecond=0) == hour
                  and any(item.get('type') in ('ADD', 'DROP', 'TRADE') for item in event.get('items', []))]
        for ident, values in current['teams'].items():
            if ident not in previous['teams']:
                continue
            for metric, threshold in [('champ', .005), ('playoff', .005), ('projWins', .1)]:
                delta = values[metric] - previous['teams'][ident][metric]
                if abs(delta) + 1e-9 < threshold:
                    continue
                confidence = 'limited historical evidence'
                if bool(current.get('backfilled')) != bool(previous.get('backfilled')):
                    cause = 'data_transition'
                    text = 'The graph switches between a reconstructed estimate and a recorded forecast. Different roster, projection and scoring inputs can create this jump; it is not evidence of a single event.'
                elif current.get('backfilled') and current['week'] != previous['week']:
                    cause = 'completed_week'
                    match = next((m for m in seed['current']['sched'] if m[0] == previous['week'] and int(ident) in m[1:3]), None)
                    if match:
                        text = f"Week {previous['week']} results entered the simulation: {names[match[1]]} scored {match[3]:g} and {names[match[2]]} scored {match[4]:g}. Updated standings and the new week's projections changed the forecast."
                    else:
                        text = 'Completed-week results, standings and the next week’s projections entered this reconstruction.'
                    confidence = 'recorded results; combined model effect'
                elif current.get('backfilled') and events:
                    cause = 'roster_recalculation'
                    facts = []
                    for event in events:
                        for item in event.get('items', []):
                            action = item.get('type')
                            if action not in ('ADD', 'DROP', 'TRADE'):
                                continue
                            player = state['players'].get(str(item.get('playerId')), {}).get('name', 'a player')
                            owner = item.get('toTeamId') if action == 'ADD' else item.get('fromTeamId')
                            facts.append(f"{names.get(owner, 'A team')} {'added' if action == 'ADD' else 'dropped' if action == 'DROP' else 'traded'} {player}")
                    text = '; '.join(facts[:4]) + '. The league was recalculated after these moves; the old 1,000-run replay also adds sampling variation, so an exact causal split is unavailable.'
                    confidence = 'verified transactions; attribution uncertain'
                elif current.get('backfilled'):
                    cause = 'simulation_variation'
                    text = 'This historical replay used a new random sample while its scoring input remained unchanged. The movement is simulation variation, not a verified change in team strength.'
                    confidence = 'verified replay-code limitation'
                else:
                    existing = current.get('changes', {}).get(ident, {})
                    cause = existing.get('cause', 'unresolved')
                    text = existing.get('text', 'The stored forecasts changed, but no sufficient input history was retained to establish why.')
                    confidence = 'recorded explanation' if existing else 'cause unverified'
                current['explanations'].setdefault(ident, {})[metric] = {
                    'cause': cause, 'text': text, 'confidence': confidence,
                    'delta': round(delta, 4), 'auditVersion': 1,
                }
    return history


if __name__ == '__main__':
    target = ROOT / 'docs/data/history.json'
    history = audit(json.loads(target.read_text()),
                    json.loads((ROOT / 'data/state/league-state.json').read_text()),
                    json.loads((ROOT / 'data/seed/2026-09-21.json').read_text()))
    target.write_text(json.dumps(history, indent=1))
    print('Explained material moves:', sum(len(metrics) for s in history['snapshots'] for metrics in s.get('explanations', {}).values()))
