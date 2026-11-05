"""Fetch the cohort's XML filings from the IRS batch zips, one member at a time.

Reads data/cohort.csv, writes data/xml/<ein>_<tax_period>.xml. Skips files already on disk,
so it's safe to rerun.
"""
import csv
from collections import defaultdict

from .cohort import DATA
from .remote_zip import RemoteZip

ZIP_URL = "https://apps.irs.gov/pub/epostcard/990/xml/{y}/{batch}.zip"


def xml_path(ein: str, tax_period: str):
    return DATA / "xml" / f"{ein}_{tax_period}.xml"


def main():
    (DATA / "xml").mkdir(parents=True, exist_ok=True)
    with (DATA / "cohort.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    todo = defaultdict(list)
    for r in rows:
        if not xml_path(r["ein"], r["tax_period"]).exists():
            todo[(r["index_year"], r["batch"])].append(r)
    got = requests = 0
    for (year, batch), items in sorted(todo.items()):
        url = ZIP_URL.format(y=year, batch=batch.upper())  # index has some "..._04a"
        try:
            z = RemoteZip(url)
        except OSError as e:
            print(f"  {batch}: can't open ({e}); {len(items)} filings skipped")
            continue
        with z:
            for r in items:
                name = f"{r['object_id']}_public.xml"
                if name not in z.names:
                    print(f"  missing {name} in {batch} ({r['name']})")
                    continue
                xml_path(r["ein"], r["tax_period"]).write_bytes(z.read(name))
                got += 1
            requests += z.raw.requests
        print(f"  {batch}: {len(items)} filings", flush=True)
    have = len(list((DATA / "xml").glob("*.xml")))
    print(f"fetched {got} filings in {requests} range requests; {have}/{len(rows)} on disk")


if __name__ == "__main__":
    main()
