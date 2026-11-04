# Mechanism pair matrix (DESIGN v0.4)

Generated mechanically (scratchpad pairs.py). For every pair of v0.4 mechanisms that shares a data object, the orchestrator checked the pair:
- **OK** means both mechanisms use the shared object with compatible semantics.
- **R-xx** means a resolution was needed; see DESIGN v0.4 §Resolutions.

Total: 138 pairs; 24 needed a resolution.

| A | B | shared objects | verdict |
|---|---|---|---|
| M-01 | M-02 | disposition | R-01 |
| M-01 | M-06 | disposition | R-01 |
| M-01 | M-13 | disposition | OK |
| M-01 | M-16 | overlay, waiver | OK |
| M-01 | M-17 | floor, overlay | R-10 |
| M-01 | M-18 | disposition, floor | OK |
| M-01 | M-21 | floor | OK |
| M-01 | M-26 | disposition, rollup | OK |
| M-02 | M-03 | fingerprint | R-12 |
| M-02 | M-04 | fingerprint | OK |
| M-02 | M-05 | quarantine, release | OK |
| M-02 | M-06 | disposition, release | R-02 |
| M-02 | M-08 | release | OK |
| M-02 | M-09 | decision, release | OK |
| M-02 | M-10 | decision | OK |
| M-02 | M-11 | release | OK |
| M-02 | M-13 | decision, disposition | OK |
| M-02 | M-14 | decision | OK |
| M-02 | M-18 | disposition | OK |
| M-02 | M-26 | disposition | OK |
| M-02 | M-28 | release | R-03 |
| M-02 | M-29 | release | R-04 |
| M-03 | M-04 | fingerprint | R-12 |
| M-03 | M-15 | anchor | OK |
| M-03 | M-19 | evidence | OK |
| M-03 | M-22 | evidence | OK |
| M-03 | M-23 | evidence, keys | OK |
| M-04 | M-05 | gated_table | OK |
| M-04 | M-06 | batch_events | OK |
| M-04 | M-08 | batch_events | OK |
| M-04 | M-10 | grants | R-05 |
| M-04 | M-14 | grants | OK |
| M-04 | M-19 | abc | OK |
| M-04 | M-21 | gated_table | OK |
| M-04 | M-24 | abc | OK |
| M-04 | M-25 | batch_events | OK |
| M-04 | M-27 | grants | R-05 |
| M-04 | M-28 | gated_table | R-06 |
| M-05 | M-06 | release | OK |
| M-05 | M-08 | consumption, release | OK |
| M-05 | M-09 | release | OK |
| M-05 | M-11 | release | OK |
| M-05 | M-21 | gated_table | OK |
| M-05 | M-28 | gated_table, release | R-11 |
| M-05 | M-29 | consumption, release | OK |
| M-06 | M-07 | dependency | OK |
| M-06 | M-08 | batch_events, release | R-02 |
| M-06 | M-09 | release | OK |
| M-06 | M-11 | release | OK |
| M-06 | M-13 | disposition | OK |
| M-06 | M-18 | disposition | OK |
| M-06 | M-25 | batch_events | OK |
| M-06 | M-26 | disposition | OK |
| M-06 | M-28 | release | OK |
| M-06 | M-29 | release | OK |
| M-07 | M-12 | registry | OK |
| M-07 | M-13 | registry | OK |
| M-07 | M-15 | registry | OK |
| M-07 | M-16 | registry | OK |
| M-07 | M-17 | registry | OK |
| M-07 | M-28 | registry | OK |
| M-08 | M-09 | release | OK |
| M-08 | M-11 | release | OK |
| M-08 | M-25 | batch_events | OK |
| M-08 | M-28 | release | R-11 |
| M-08 | M-29 | consumption, release | OK |
| M-09 | M-10 | decision, procs | OK |
| M-09 | M-11 | manifest, procs, release | R-07 |
| M-09 | M-12 | manifest, procs | OK |
| M-09 | M-13 | decision | OK |
| M-09 | M-14 | decision | OK |
| M-09 | M-15 | manifest | OK |
| M-09 | M-25 | procs | OK |
| M-09 | M-28 | release | OK |
| M-09 | M-29 | release | OK |
| M-10 | M-11 | procs | OK |
| M-10 | M-12 | procs | OK |
| M-10 | M-13 | decision | OK |
| M-10 | M-14 | decision, grants | OK |
| M-10 | M-19 | authority | R-08 |
| M-10 | M-20 | authority | R-08 |
| M-10 | M-21 | authority | R-08 |
| M-10 | M-22 | authority | OK |
| M-10 | M-23 | authority | OK |
| M-10 | M-24 | authority | OK |
| M-10 | M-25 | procs | OK |
| M-10 | M-27 | grants | OK |
| M-11 | M-12 | manifest, procs | OK |
| M-11 | M-15 | manifest | OK |
| M-11 | M-25 | procs | OK |
| M-11 | M-28 | release | OK |
| M-11 | M-29 | release | OK |
| M-12 | M-13 | registry | OK |
| M-12 | M-15 | manifest, registry | R-09 |
| M-12 | M-16 | registry | OK |
| M-12 | M-17 | registry | OK |
| M-12 | M-25 | procs | OK |
| M-12 | M-28 | registry | OK |
| M-13 | M-14 | decision | OK |
| M-13 | M-15 | registry | R-09 |
| M-13 | M-16 | registry | OK |
| M-13 | M-17 | registry | OK |
| M-13 | M-18 | disposition | OK |
| M-13 | M-26 | disposition | OK |
| M-13 | M-28 | pinning, registry | OK |
| M-14 | M-15 | duties | OK |
| M-14 | M-20 | duties | OK |
| M-14 | M-27 | duties, grants | OK |
| M-15 | M-16 | registry | R-09 |
| M-15 | M-17 | registry | OK |
| M-15 | M-20 | duties | OK |
| M-15 | M-27 | duties | OK |
| M-15 | M-28 | registry | OK |
| M-16 | M-17 | overlay, registry | OK |
| M-16 | M-28 | registry | OK |
| M-17 | M-18 | floor | R-10 |
| M-17 | M-21 | floor | R-10 |
| M-17 | M-28 | registry | OK |
| M-18 | M-21 | floor | OK |
| M-18 | M-26 | disposition | OK |
| M-19 | M-20 | authority | OK |
| M-19 | M-21 | authority | OK |
| M-19 | M-22 | authority, evidence, presentation | OK |
| M-19 | M-23 | authority, evidence | OK |
| M-19 | M-24 | abc, authority | OK |
| M-20 | M-21 | authority | OK |
| M-20 | M-22 | authority | OK |
| M-20 | M-23 | authority | OK |
| M-20 | M-24 | authority | OK |
| M-20 | M-27 | duties | OK |
| M-21 | M-22 | authority | OK |
| M-21 | M-23 | authority | OK |
| M-21 | M-24 | authority | OK |
| M-21 | M-28 | gated_table | OK |
| M-22 | M-23 | authority, evidence | OK |
| M-22 | M-24 | authority | OK |
| M-23 | M-24 | authority | OK |
| M-28 | M-29 | release | R-04 |
