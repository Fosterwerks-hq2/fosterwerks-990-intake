"""Pick the cohort: Detroit 501(c)(3)s that e-filed a 990 or 990-EZ, and where their XML lives.

Sources (both IRS, both public):
- Exempt Organizations Business Master File, Michigan: irs.gov/pub/irs-soi/eo_mi.csv
- Yearly e-file indexes: apps.irs.gov/pub/epostcard/990/xml/<year>/index_<year>.csv
  (streamed and filtered, never saved: they're 60-100 MB each)

Writes data/candidates.csv (every Detroit filer found) and data/cohort.csv (the sample).
"""
import csv
import hashlib
import io
import subprocess
from collections import defaultdict
from pathlib import Path

from .remote_zip import USER_AGENT

DATA = Path(__file__).resolve().parent.parent / "data"
BMF_URL = "https://www.irs.gov/pub/irs-soi/eo_mi.csv"
INDEX_URL = "https://apps.irs.gov/pub/epostcard/990/xml/{y}/index_{y}.csv"
YEARS = (2024, 2025, 2026)
FORMS = {"990", "990EZ"}

# Always included, whatever the sample draws: they anchor the eval.
ANCHORS = {
    "010559608": "known figures from the TechTown triage (revenue $24.0M FY2023)",
}
COHORT_SIZE = 60
EZ_SHARE = 8  # how many 990-EZ filers to include (no employee count: an eval trap)


class _get:
    """Stream a URL through curl. Python's own TLS stack drops large IRS downloads on this
    machine (SSL RECORD_LAYER_FAILURE, 2026-11-05); curl doesn't."""

    def __init__(self, url: str):
        self.proc = subprocess.Popen(["curl", "-sSfL", "--retry", "3", "-A", USER_AGENT, url],
                                     stdout=subprocess.PIPE)

    def __enter__(self): return self.proc.stdout

    def __exit__(self, *exc):
        self.proc.stdout.close()
        if self.proc.wait() not in (0, -13, 23):  # 23/-13: we closed the pipe early
            raise IOError(f"curl failed: {self.proc.args[-1]}")


def detroit_orgs() -> dict[str, dict]:
    path = DATA / "eo_mi.csv"
    if not path.exists():
        DATA.mkdir(exist_ok=True)
        with _get(BMF_URL) as r:
            path.write_bytes(r.read())
    orgs = {}
    with path.open(newline="", encoding="latin-1") as f:
        for row in csv.DictReader(f):
            if row["CITY"].strip().upper() == "DETROIT" and row["SUBSECTION"] == "03":
                orgs[row["EIN"]] = {"ein": row["EIN"], "bmf_name": row["NAME"].strip(),
                                    "ntee": row["NTEE_CD"].strip(), "zip": row["ZIP"][:5],
                                    "bmf_revenue": row["REVENUE_AMT"] or "0"}
    return orgs


def scan_indexes(eins: set[str]) -> list[dict]:
    hits = []
    for y in YEARS:
        print(f"  streaming index_{y}.csv ...", flush=True)
        with _get(INDEX_URL.format(y=y)) as r:
            for row in csv.DictReader(io.TextIOWrapper(r, encoding="utf-8-sig", newline="")):
                if row["EIN"] in eins and row["RETURN_TYPE"] in FORMS:
                    hits.append({"ein": row["EIN"], "tax_period": row["TAX_PERIOD"],
                                 "form": row["RETURN_TYPE"], "name": row["TAXPAYER_NAME"].strip(),
                                 "object_id": row["OBJECT_ID"], "batch": row["XML_BATCH_ID"],
                                 "index_year": y})
    return hits


def _rank(ein: str) -> str:
    """Deterministic pseudo-random order, so the sample is reproducible."""
    return hashlib.sha256(f"990-intake:{ein}".encode()).hexdigest()


def pick(orgs: dict, hits: list[dict]) -> list[dict]:
    """Latest filing per EIN (plus the one before it, for year-over-year questions).
    Sample spread across NTEE major groups, with a fixed share of 990-EZ filers."""
    by_ein = defaultdict(list)
    for h in hits:
        by_ein[h["ein"]].append(h)
    for v in by_ein.values():  # dedupe amended/duplicate rows: keep one per tax period
        v.sort(key=lambda h: (h["tax_period"], h["index_year"]), reverse=True)

    def latest_form(e): return by_ein[e][0]["form"]
    full = sorted((e for e in by_ein if latest_form(e) == "990"), key=_rank)
    ez = sorted((e for e in by_ein if latest_form(e) == "990EZ"), key=_rank)

    chosen = [e for e in ANCHORS if e in by_ein]
    groups = defaultdict(list)
    for e in full:
        if e not in chosen:
            groups[(orgs[e]["ntee"] or "?")[0]].append(e)
    # round-robin across NTEE major groups
    target_full = COHORT_SIZE - EZ_SHARE
    while len(chosen) < target_full and any(groups.values()):
        for g in sorted(groups):
            if groups[g] and len(chosen) < target_full:
                chosen.append(groups[g].pop(0))
    chosen += ez[:EZ_SHARE]

    rows = []
    for e in chosen:
        seen = set()
        for h in by_ein[e]:
            if h["tax_period"] in seen:
                continue
            seen.add(h["tax_period"])
            rows.append({**h, "ntee": orgs[e]["ntee"], "bmf_name": orgs[e]["bmf_name"],
                         "anchor": ANCHORS.get(e, "")})
            if len(seen) == 2:
                break
    return rows


def _write(path: Path, rows: list[dict]):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    orgs = detroit_orgs()
    print(f"{len(orgs)} Detroit 501(c)(3)s in the IRS master file")
    cand = DATA / "candidates.csv"
    if cand.exists():
        with cand.open(newline="") as f:
            hits = [dict(r, index_year=int(r["index_year"])) for r in csv.DictReader(f)]
    else:
        hits = scan_indexes(set(orgs))
        _write(cand, hits)
    print(f"{len(hits)} e-filed 990/990-EZ returns from {len({h['ein'] for h in hits})} of them")
    rows = pick(orgs, hits)
    _write(DATA / "cohort.csv", rows)
    forms = defaultdict(int)
    for r in rows:
        forms[r["form"]] += 1
    print(f"cohort: {len({r['ein'] for r in rows})} orgs, {len(rows)} filings {dict(forms)}"
          f" -> data/cohort.csv")


if __name__ == "__main__":
    main()
