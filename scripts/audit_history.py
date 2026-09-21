"""Explain material stored movements without inventing historical causality.

Legacy backfill ran with phase_started=False and a new random seed per event.
Its score-only/lineup-only updates therefore changed sampling, not model inputs.
Keep audit evidence in metadata; use plain football language on hover.
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
        if current.get('explanations') and current.get('backfilled') and all(
                entry.get('auditVersion', 0) >= 4
                for metrics in current['explanations'].values() for entry in metrics.values()):
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
                show_tooltip = False
                if bool(current.get('backfilled')) != bool(previous.get('backfilled')):
                    cause = 'data_transition'
                    text = 'Rosters, scores and player forecasts differ at this point; no single confirmed football event explains the change.'
                elif current.get('backfilled') and current['week'] != previous['week']:
                    cause = 'completed_week'
                    match = next((m for m in seed['current']['sched'] if m[0] == previous['week'] and int(ident) in m[1:3]), None)
                    if match:
                        home = int(ident) == match[1]
                        own, other = (match[3], match[4]) if home else (match[4], match[3])
                        opponent = names[match[2] if home else match[1]]
                        result = 'beat' if own > other else 'lost to' if own < other else 'tied'
                        effect = ('The win helps their playoff position' if own > other else
                                  'The loss hurts their playoff position' if own < other else
                                  'The tie changes their playoff position')
                        text = f"{names[int(ident)]} {result} {opponent} {own:.1f}–{other:.1f} in Week {previous['week']}. {effect}; points scored also count towards seeding."
                        show_tooltip = True
                    else:
                        text = 'The week’s results changed the standings; next week’s player forecasts also changed the outlook for the playoff race.'
                    confidence = 'recorded results; combined model effect'
                elif current.get('backfilled') and events:
                    cause = 'roster_recalculation'
                    facts = []
                    # Put this team's moves before league-wide context.
                    events = sorted(events, key=lambda e: not any(int(ident) in
                        (i.get('fromTeamId'), i.get('toTeamId')) for i in e.get('items', [])))
                    direct = False
                    for event in events:
                        for item in event.get('items', []):
                            action = item.get('type')
                            if action not in ('ADD', 'DROP', 'TRADE'):
                                continue
                            player = state['players'].get(str(item.get('playerId')), {}).get('name', 'a player')
                            owner = item.get('toTeamId') if action == 'ADD' else item.get('fromTeamId')
                            direct = direct or int(ident) in (item.get('fromTeamId'), item.get('toTeamId'))
                            facts.append(f"{names.get(owner, 'A team')} {'added' if action == 'ADD' else 'dropped' if action == 'DROP' else 'traded'} {player}")
                    text = '; '.join(facts[:4]) + '. '
                    text += ('The roster options changed, but a stronger or weaker starting lineup is not confirmed.' if direct
                             else f"No direct effect on {names.get(int(ident), 'this team')}’s outlook is confirmed.")
                    confidence = 'verified transactions; attribution uncertain'
                elif current.get('backfilled'):
                    cause = 'simulation_variation'
                    text = 'No confirmed roster move, injury update or scoring change explains this shift in the team’s outlook.'
                    confidence = 'verified replay-code limitation'
                else:
                    existing = current.get('changes', {}).get(ident, {})
                    cause = existing.get('cause', 'unresolved')
                    text = existing.get('text', 'No confirmed roster move, injury update or result explains this change.')
                    confidence = 'recorded explanation' if existing else 'cause unverified'
                    show_tooltip = existing.get('showTooltip', False)
                current['explanations'].setdefault(ident, {})[metric] = {
                    'cause': cause, 'text': text, 'confidence': confidence,
                    'delta': round(delta, 4), 'auditVersion': 4,
                    'showTooltip': show_tooltip,
                }
    return history


if __name__ == '__main__':
    target = ROOT / 'docs/data/history.json'
    history = audit(json.loads(target.read_text()),
                    json.loads((ROOT / 'data/state/league-state.json').read_text()),
                    json.loads((ROOT / 'data/seed/2026-09-21.json').read_text()))
    target.write_text(json.dumps(history, indent=1))
    print('Explained material moves:', sum(len(metrics) for s in history['snapshots'] for metrics in s.get('explanations', {}).values()))
