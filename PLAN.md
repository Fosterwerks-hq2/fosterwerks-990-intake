# 990 intake agent: plan

*Started 2026-11-05. Practice project for PRACTICE.md #1 (MCP server), #2 (Claude Agent SDK),
#3 (RAG). Public repo + write-up on fosterwerks.com/work, next to the triage benchmark.*

## What it does

Give it a Detroit nonprofit's name. The agent finds the organization's IRS Form 990 filings,
answers the prospect questions Fosterwerks actually asks (size, budget, staff, what they do,
regulated data), cites the filing section behind every answer, and proposes a prospect record.
**Nothing is saved until a person approves it.** Every tool call is logged.

It is the TechTown triage done properly: one agent, real retrieval, typed output, a human gate,
and a measured eval instead of a calibration pass after the fact.

## Data (public, no scraping)

- **IRS Form 990 e-file XML**, published by the IRS at `apps.irs.gov/pub/epostcard/990/xml/<year>/`.
  The yearly index CSV lists every return and the batch zip it's in. The server supports HTTP
  range requests (checked 2026-11-05), so we read the zip's directory and pull single filings
  without downloading the 100 MB+ batches. The Chromebook has ~5 GB free: keep it that way.
- **Cohort:** Detroit-area filers, found by EIN through ProPublica's Nonprofit Explorer JSON API
  (search by city/state). The API is open; its PDF downloads are behind a security check, so we
  don't use them.
- **Answer key:** the XML's own fields (total revenue, expenses, assets, employee count,
  mission, program descriptions). The eval grades the agent against them automatically.
- Cohort size: about 60 organizations, 1-2 filing years each. Cached locally, gitignored, with
  a script to rebuild.

## Pieces

| Piece | What | Practice item |
|---|---|---|
| `ingest/` | Pick the cohort, fetch XML by range request, parse into (a) structured facts and (b) text sections with stable IDs (`<ein>/<year>/partIII/prog2`) | |
| `retrieval/` | SQLite FTS5 (BM25) index over the sections, filterable by EIN and year. No embedding service; lexical retrieval is the honest fit for form text full of exact terms. Embeddings are a later comparison if BM25 misses | #3 RAG |
| `mcp_server/` | Standalone MCP server (official `mcp` Python SDK, stdio). Tools: `find_org`, `search_filings`, `get_section`, `propose_prospect`, `list_review_queue`. No financials lookup (decided 2026-11-05): figures must come from sections; the answer key stays with the eval. Works from Claude Code too (`.mcp.json`), which is part of the demo | #1 MCP |
| `agent/` | Claude Agent SDK app. Built-in tools (Bash, Write, web) removed; only the MCP tools. Read tools auto-allowed; `propose_prospect` is gated by `can_use_tool` and asks the person in the terminal. Hooks write every tool call and result to `logs/*.jsonl`. Output is a typed `ProspectRecord` (JSON schema via `output_format`) where every claim carries a section ID. `max_budget_usd` caps each run | #2 Agent SDK |
| `review` CLI | List, approve, reject the queue; who and when recorded (same shape as Navira's review queue) | |
| `eval/` | ~30 questions generated from the XML truth + hand-written traps: org with no e-filed 990, a 990-EZ (no employee count), two orgs with near-identical names, a filing whose text contains an instruction ("ignore previous instructions...") planted in the local copy. Grades: numeric match within rounding, citation points to a section that contains the value, refuses to guess when data is missing, never calls `propose_prospect` without approval. Reports accuracy, cost and latency per run | evals |

## Build order (each step ends runnable)

1. **Ingest + retrieval.** No API spend. Done when `search "youth mentoring" --ein ...` returns cited sections.
2. **MCP server.** Done when Claude Code can call the tools from `.mcp.json` and answer "what was Allied Media Projects' 2023 revenue?" with a section ID.
3. **Agent SDK app + approval gate + logging.** Done when a run proposes a record, the terminal asks, and the log shows every call.
4. **Eval.** Done when one command prints the scorecard and cost.
5. **Write-up** for fosterwerks.com/work + master.md bullet + move PRACTICE.md items to Done.

## Model and cost

Claude Opus 5.5 at low effort for agent runs, with prompt caching on the system prompt and
tool definitions, and a custom system prompt (not the Claude Code preset, which is much
larger). Each run resends the conversation on every tool call, so expect roughly 3-10 cents a
run and $1-3 for a 30-question eval. That's a real share of the $5 budget: measure the first
five runs before the full set, and use `max_budget_usd` on every run. The Anthropic key lives in `~/job-search/.env`; this repo gets its own
gitignored `.env`.

## Open decisions (Evan)

- Repo goes public in Fosterwerks-hq2 when step 3 works (not before).
- Whether the write-up names real Detroit nonprofits (public filings, but they're also
  prospects) or anonymizes them in the published examples.
