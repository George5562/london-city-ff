# Jev prediction classifier contract

The Decision Agent Business attachment is a guarded local wrapper. Its
Keychain-only routing is not portable to GitHub Actions, so this project keeps
the same operating pattern in `prediction_events.py`: classify first, explain
second.

The typed cause is one of `score_update`, `trade`, `roster_move`,
`roster_availability`, `external_opportunity`, `projection_rerating`, or
`standings_context`. The deterministic tree is always available. When
`OPENROUTER_API_KEY` is configured, `typesafe/jev-1.13` reviews only the
compact team-scoped facts and may select one of those labels. A separate
`openai/gpt-4.1-nano` request writes the short tooltip sentence.

No model decides the odds; the simulator and recorded ESPN deltas do. The
model may not invent an event, player, number, or causal relationship absent
from the supplied facts.
