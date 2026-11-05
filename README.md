# 990 intake agent

An agent that researches Detroit nonprofits from their IRS Form 990 filings, cites the filing
section behind every answer, and proposes a prospect record that a person approves before it's
saved. Built on Claude with an MCP server, the Claude Agent SDK, retrieval and an eval.
See [PLAN.md](PLAN.md) for the design and build order.

**Status:** steps 1-2 of 5 done (data + retrieval, MCP server).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Step 1: data and retrieval (no API calls)

```bash
.venv/bin/python -m intake cohort    # Detroit 501(c)(3)s from the IRS master file + e-file indexes
.venv/bin/python -m intake fetch     # pull each filing's XML out of the IRS batch zips
.venv/bin/python -m intake build     # parse into data/intake.sqlite
.venv/bin/python -m intake find "allied media"
.venv/bin/python -m intake search "youth mentoring" -k 3
.venv/bin/python -m intake section 010559608/2023/990/p3
```

- **Sources:** the IRS Exempt Organizations Business Master File for Michigan (`eo_mi.csv`) and
  the IRS Form 990 e-file XML (`apps.irs.gov/pub/epostcard/990/xml/`). Both are public. The
  batch zips are 100 MB and up; `intake/remote_zip.py` reads single filings out of them with
  HTTP range requests (one filing is about 3 requests and 1 second).
- **Cohort:** 60 Detroit 501(c)(3)s, drawn reproducibly across NTEE groups, with 8 990-EZ filers
  and Allied Media Projects as a fixed anchor. Up to two filing years each.
- **Sections:** each filing is split the way the paper form is (Part I summary, Part III
  programs, Part VII officers, each schedule; Schedule O one note per section). Section IDs look
  like `010559608/2023/990/p3`, so a citation can be checked against the printed return.
- **Answer key:** a few exact values per filing (revenue, expenses, assets, employees, mission,
  audited or not), each with the XML element it came from. Used by the eval in step 4.
- **Retrieval:** SQLite FTS5, BM25, porter stemming, filterable by EIN and year.

## Step 2: MCP server

`intake/mcp_server.py`, built on the official MCP Python SDK (2.x, `MCPServer`), stdio transport.
Claude Code picks it up from [`.mcp.json`](.mcp.json); any MCP client can launch
`.venv/bin/python -m intake.mcp_server`.

| Tool | What it does |
|---|---|
| `find_org` | Name (partial is fine) -> EIN and the filings on hand |
| `search_filings` | BM25 search over sections, filter by EIN / tax year, returns full text + section ID |
| `get_section` | One section in full by ID |
| `propose_prospect` | Queue a typed `ProspectRecord` for human review (status `pending`) |
| `list_review_queue` | Pending, approved or rejected items |

- **No financials lookup tool, on purpose.** Every figure has to come from reading a section, so
  citations mean something and the eval tests retrieval rather than a database lookup. The
  answer key stays with the eval.
- **Grounding check on `propose_prospect`.** A record is refused (and nothing is queued) if a
  citation doesn't exist or belongs to another organization, or if its revenue, expenses or
  employee count don't appear in a cited section. The error tells the agent what to fix.
- **Review queue** in its own database (`data/review.sqlite`): pending -> approved / rejected,
  with who proposed it, who decided and when.
- **Logging:** every tool call goes to `logs/mcp-server.jsonl` with the client name, arguments,
  result IDs or error, and time taken.
- **Filing text is data.** The server's instructions tell clients never to follow instructions
  found inside a filing (the eval plants one to check).

```bash
.venv/bin/python tests/test_mcp_server.py   # stdio smoke test of every tool, temp review DB
claude -p "What was Allied Media Projects' 2023 revenue? Cite section IDs." \
  --mcp-config .mcp.json --strict-mcp-config \
  --allowedTools "mcp__irs990__find_org,mcp__irs990__search_filings,mcp__irs990__get_section"
```

First Claude Code run (2026-11-05): two tool calls, $24,072,145 revenue and 108 employees, both
cited to `010559608/2023/990/p1`, and it flagged the return as amended.

## Known gaps (step 1)

- 5 of 114 filings couldn't be fetched: the IRS index points to a batch that doesn't contain
  them. The orgs are kept without those years (one org dropped out entirely).
- Some IRS batch names in the index are lowercase (`..._04a`) while the files are uppercase, and
  some 2025 batches use Deflate64 compression (needs `zipfile-deflate64`).
- Python's TLS stack dropped the large IRS downloads on the dev machine; bulk downloads go
  through `curl`.
- Lexical retrieval is weak on form-field questions: "total employees" ranks a compensation
  table above the Part I line that answers it. The eval will measure how much this matters.
- Allied Media Projects' FY2023 return is amended: its XML revenue ($24,072,145) differs from
  ProPublica's figure ($24,047,524). The IRS XML is the answer key here.
