# 990 intake agent

An agent that researches Detroit nonprofits from their IRS Form 990 filings, cites the filing
section behind every answer, and proposes a prospect record that a person approves before it's
saved. Built on Claude with an MCP server, the Claude Agent SDK, retrieval and an eval.
See [PLAN.md](PLAN.md) for the design and build order.

**Status:** step 1 of 5 done (data + retrieval).

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
