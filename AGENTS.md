# Instructions for AI agents

This document is agent-agnostic. Read it if you are Claude Code, Cursor, Windsurf,
Zed, Codex, Copilot, an MCP host, or any other agent working with this repository.

## What this is

`vest` is an attribution pipeline for A-share (China mainland equities) sector/theme
price moves. It answers: **"why did this theme surge, and is that surge a real cash-flow
change?"**

Its output is a **research card**, not a trading signal. It never produces position
sizes, entry/exit points, target prices, or predictions.

## The one idea that matters

Asking "why did X surge?" *after* X surged always produces a plausible story. That is
narrative fallacy, and it cannot be prevented by prompting an agent to "be objective."

The defense here is mechanical. Every piece of evidence is bucketed by its publication
timestamp relative to the trading session of the anomaly day `D` (09:30–15:00 Beijing):

| Bucket | Condition | Permitted use |
|---|---|---|
| `pre_session` | before `D`, or before 09:30 on `D` | **may be cited as a cause** |
| `intraday` | 09:30–15:00 on `D` | may be a cause *or* media coverage of the rise — judge each item and say why |
| `post_close` | after 15:00 on `D`, or after `D` | **physically can only be a consequence. Never cite as a cause.** |
| `undetermined` | day-level precision and same date as `D` | **ordering unknowable — must be declared as such** |

Corrupting this is the single most damaging mistake available in this codebase. A
`post_close` headline placed in the "cause" slot produces a confident, fluent, wrong
research card.

## Two ways to use it

### 1. MCP server (preferred — no shell needed)

Install first:

```bash
git clone <repo-url> && cd <repo-dir>
uv venv && uv pip install -e ".[mcp]"
```

Launch command (pick one, all verified):

| Form | Command | Use when |
|---|---|---|
| Absolute path | `<repo>/.venv/bin/vest-mcp` | **Required for GUI-launched clients** (Claude Desktop, etc.) that do not inherit your shell PATH |
| `uv run` | `uv run --directory <repo> vest-mcp` | Only if the client's process can find `uv` |
| Module | `<repo>/.venv/bin/python -m vest.mcp_server` | Equivalent fallback |

Tools exposed: `snapshot`, `anomalies`, `evidence`, `classify_timing`, `save_card`,
`list_cards`, `review_card`.

The causal discipline above is also delivered in the server's MCP `instructions` field,
which compliant clients surface to the model. **Follow it.** The tools return data, not
judgment — the discipline is what turns one into the other.

### 2. CLI (any agent with shell access)

```bash
.venv/bin/vest snapshot 2026-10-14        # ingest a trading day
.venv/bin/vest anomalies 2026-10-14       # ranked theme moves
.venv/bin/vest evidence "AI短剧" 2026-10-14   # bucketed evidence bundle (JSON)
.venv/bin/vest save-card cards/x.json     # persist a research card
.venv/bin/vest cards                      # list cards + review status
.venv/bin/vest review <card_id> "outcome" # close the loop
```

On Windows use `.venv\Scripts\vest`.

## Standard workflow

1. `anomalies` for the target day → pick themes with the most co-moving stocks.
2. `evidence` for each → this already applies the bucketing for you.
3. Write the card: four-way classification, counter-argument, falsifiable check,
   fundamental verdict.
4. `save_card`.
5. Set `review_at` 5 trading days out, and actually fill it in when due.

Step 5 is what makes this a ledger of judgment rather than an archive of moods.

## Writing a card

See [`skill/ashare-theme-attribution/references/card-schema.md`](skill/ashare-theme-attribution/references/card-schema.md)
for field-by-field rules and
[`skill/ashare-theme-attribution/assets/card-example.json`](skill/ashare-theme-attribution/assets/card-example.json)
for a real worked example.

Required fields: `theme`, `anomaly_date`, `anomaly_detail`, `evidence`,
`classification` (事实/推论/观点/待验证), `counter_argument`, `falsifiable_check`,
`fundamental_verdict` ∈ {基本面变化, 情绪资金驱动, 待验证, 噪声}, `followup_queue`,
`review_at`.

Additional rules the tools cannot enforce for you:

- **`editorial_tags` are third-party opinion.** They are hand-maintained theme labels
  from Tonghuashun's editorial desk. They belong in `观点` with a source, never in `事实`.
- **"No cause found" is a finding.** If the `pre_session` bucket contains no
  fundamental catalyst (no contract, order, policy, or earnings event), say so
  explicitly in the card. That absence is the strongest available evidence that the
  move was flows, not fundamentals.
- **`情绪资金驱动` does not mean "not worth researching."** Whether the business is
  worth its price is an independent question. Put it in `followup_queue`; do not let
  one theme-day skip the fundamentals homework.
- **Record being wrong.** When filling `review_at`, honestly log misjudgment. A ledger
  that reads 100% correct has no information content.
- **Cross-check for adverse signals.** If a constituent published a shareholder
  reduction plan, earnings warning, or investigation notice *before* the price move,
  industry capital acted against the market's mood. That belongs in `counter_argument`.

## Data timing — do not skip this

Tonghuashun's hot-stocks endpoint does **not** populate price fields
(`zhangfu` / `close` / `huanshou` / `chengjiaoe`) until after settlement, which happens
between 17:14 on day `T` and 12:43 on day `T+1` (measured, exact boundary unknown).

Therefore: **to attribute day `T`, run on day `T+1`.** Requesting `snapshot` for an
unsettled day raises an error rather than writing NULL prices — this is deliberate.
Writing NULL prices makes `anomalies` return zero results, which is indistinguishable
from "nothing moved today." Do not loosen the guard.

`snapshot` for a non-trading day returns `hot_stock: 0`. Treat that as no data, not an
error.

## Verifying your setup

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

13 tests. They cover session-boundary edges, and assert that bucketing is **identical
across host timezones** — an earlier version interpreted Unix milliseconds in local time,
which silently laundered `undetermined` evidence into `pre_session` on non-Beijing hosts.

Four tests require the optional `mcp` extra and skip otherwise. One of them performs a
real MCP stdio handshake, so `list_tools` and `initialize` are verified end to end.

## Repository layout

- `src/vest/` — Python package: `sources` (data), `store` (DuckDB), `snapshot` (ingest),
  `attribute` (bucketing + recall + cards), `cli`, `mcp_server`
- `skill/ashare-theme-attribution/` — Agent Skills-format skill (SKILL.md + references +
  a stdlib-only fallback bucketing script). Usable as-is in Claude Code / Claude.ai.
- `tests/` — timing and MCP tests
- `cards/` — research cards (JSON)
- `data/` — DuckDB, gitignored

## Known limitations

- A-share only. Trading-session times and Beijing timezone are hardcoded.
- Anomaly detection uses fixed thresholds until ≥20 trading days of snapshots exist;
  `anomalies` reports `zscore_available: false` honestly. Do not claim z-score otherwise.
- **Evidence sets are not reproducible.** The Eastmoney news endpoint returns a rolling
  top-N, so a re-run later yields a different article set (measured: one item aged out,
  one aged in, within 46 minutes). The `evidence` field stored *inside the card* is the
  authoritative record. Copy timestamps and `ts_precision` fully; do not keep titles only.
- Upstream endpoints can change without notice. Documented failure modes and
  self-check commands:
  [`references/failure-traps.md`](skill/ashare-theme-attribution/references/failure-traps.md)
