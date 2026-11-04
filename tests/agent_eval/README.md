# Deterministic agent evaluations (Part C, WS4)

These tests check the **agent's** behaviour end to end against expectations written **from the fixture recipe**,
never from a previous run of the agent (red-team F8). The behaviours covered are rule dispositions, unit end
states, blackboard posts by seat, memory, and the effect of a known issue.

- `scenarios.yaml` holds each scenario's recipe: the files it feeds in, and any human decisions or memory actions it takes.
- Expectations are derived by hand from the defects each recipe seeds. A `mutation_of` scenario is a known scenario with exactly one seed added or removed. Its expected delta is that single rule.
- The suite includes negative controls: a clean recipe, a forged seat, a self-approved known issue, and a known issue that tries to change a disposition.
- Determinism is checked separately. Each scenario runs twice in two fresh control planes and must produce byte-identical canonical digests. A digest covers results, units, measures, the board and memory, with timestamps and surrogate ids removed.
- The LLM provider is NullProvider throughout, so the agent's behaviour under test is deterministic code only.
