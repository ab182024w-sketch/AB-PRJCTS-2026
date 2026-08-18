# Phase 0 download — 2025, weeks 1–18

Produced by `python -m pipeline.download --season 2025 --weeks 1-18`.
The CSVs themselves are not committed (`data/` is gitignored); this is the record of what the run produced.

| | |
| --- | --- |
| Files requested | 152 (18 weeks × 8 positions = 144, plus 8 `{POS}_season.csv`) |
| Files downloaded | 152 |
| Missing at source (404) | 0 |
| Errors | 0 |
| Total data rows (excluding headers) | 49,658 |
| On-disk size | 3.5 MB |

A second identical run reported `unchanged: 152` and rewrote nothing — the idempotency requirement, verified rather than asserted.

## Weekly file shape

| | |
| --- | --- |
| Player-week rows (weeks 1–18, all positions) | 47,032 |
| Bye rows (`PlayerOpponent = 'Bye'`) | 12,619 |
| Duplicate `(week, PlayerId)` keys | 0 |
| Null `PlayerId` or `Pos` | 0 |
| Distinct `Team` values | 32 NFL abbreviations + `FA` |
| Rows per week | 2,598 (wk 1) → 2,626 (wk 15–18) |

The tall/EAV expansion of those rows is 885,174 stat rows, with **0 cast failures** — every non-blank
numeric cell parsed.
