"""Command line for step 1.

  python -m intake cohort                    pick the Detroit cohort (IRS master file + indexes)
  python -m intake fetch                     pull the cohort's XML by range request
  python -m intake build                     parse XML -> data/intake.sqlite
  python -m intake find "allied media"       look up an organization by name
  python -m intake search "youth mentoring" [--ein EIN] [--year YYYY] [-k N]
  python -m intake section <section id>      print one section in full
"""
import argparse
import textwrap

from . import cohort, fetch, store


def main():
    ap = argparse.ArgumentParser(prog="python -m intake", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cohort")
    sub.add_parser("fetch")
    sub.add_parser("build")
    p = sub.add_parser("find")
    p.add_argument("name")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--ein")
    p.add_argument("--year", type=int)
    p.add_argument("-k", type=int, default=5)
    p = sub.add_parser("section")
    p.add_argument("id")
    a = ap.parse_args()

    if a.cmd == "cohort":
        cohort.main()
    elif a.cmd == "fetch":
        fetch.main()
    elif a.cmd == "build":
        print(store.build())
    else:
        con = store.connect()
        if a.cmd == "find":
            for o in store.find_org(con, a.name):
                print(f"{o['ein']}  {o['name']}  [{o['ntee']}]  filings: {', '.join(o['filings'])}")
        elif a.cmd == "search":
            for r in store.search(con, a.query, a.ein, a.year, a.k):
                print(f"\n{r['id']}  (bm25 {r['score']:.2f})  {r['title']}")
                hit = next((ln for ln in r["text"].splitlines()
                            if any(w in ln.lower() for w in a.query.lower().split())),
                           r["text"].splitlines()[0])
                print(textwrap.indent(textwrap.shorten(hit, 220), "    "))
        elif a.cmd == "section":
            s = store.get_section(con, a.id)
            print(f"{s['id']}\n{s['title']}\n\n{s['text']}" if s else "no such section")


if __name__ == "__main__":
    main()
