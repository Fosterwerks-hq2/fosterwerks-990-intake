"""SQLite store + BM25 retrieval over filing sections.

Tables
  orgs       ein, name, ntee                     one row per organization
  filings    ein, tax_year, form, facts (JSON)   the answer key, one row per filing
  sections   id, ein, tax_year, form, title, text
  sections_fts / orgs_fts                        FTS5 indexes (porter stemming)

Retrieval is lexical (BM25). 990 text is full of exact terms (form lines, program names,
dollar figures), which BM25 handles well; whether embeddings would help is a question for the
eval, not an assumption.
"""
import csv
import json
import re
import sqlite3
from pathlib import Path

from .cohort import DATA
from .parse import parse

DB = DATA / "intake.sqlite"
SCHEMA = """
CREATE TABLE orgs (ein TEXT PRIMARY KEY, name TEXT NOT NULL, ntee TEXT);
CREATE TABLE filings (ein TEXT NOT NULL, tax_year INTEGER NOT NULL, form TEXT NOT NULL,
                      facts TEXT NOT NULL, PRIMARY KEY (ein, tax_year, form));
CREATE TABLE sections (id TEXT PRIMARY KEY, ein TEXT NOT NULL, tax_year INTEGER NOT NULL,
                       form TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL);
CREATE VIRTUAL TABLE sections_fts USING fts5(title, text, content='sections',
                                             tokenize='porter unicode61');
CREATE VIRTUAL TABLE orgs_fts USING fts5(ein UNINDEXED, name, tokenize='porter unicode61');
"""


def connect(path: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def build(path: Path = DB) -> dict:
    """Rebuild the database from data/xml. Returns counts."""
    if path.exists():
        path.unlink()
    with (DATA / "cohort.csv").open(newline="") as f:
        ntee = {r["ein"]: r["ntee"] for r in csv.DictReader(f)}
    con = connect(path)
    con.executescript(SCHEMA)
    names: dict[str, tuple[int, str]] = {}
    n_sections = 0
    for xml in sorted((DATA / "xml").glob("*.xml")):
        f = parse(xml.read_bytes())
        con.execute("INSERT OR REPLACE INTO filings VALUES (?,?,?,?)",
                    (f.ein, f.tax_year, f.form, json.dumps(f.facts)))
        for s in f.sections:
            con.execute("INSERT INTO sections VALUES (?,?,?,?,?,?)",
                        (s.id, f.ein, f.tax_year, f.form, s.title, s.text))
            n_sections += 1
        if f.ein not in names or f.tax_year > names[f.ein][0]:  # latest filing's name wins
            names[f.ein] = (f.tax_year, f.name)
    for ein, (_, name) in names.items():
        con.execute("INSERT INTO orgs VALUES (?,?,?)", (ein, name, ntee.get(ein)))
        con.execute("INSERT INTO orgs_fts VALUES (?,?)", (ein, name))
    con.execute("INSERT INTO sections_fts(rowid, title, text) "
                "SELECT rowid, title, text FROM sections")
    con.commit()
    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("orgs", "filings", "sections")}
    con.close()
    return counts


def _match(query: str) -> str:
    """User text -> safe FTS5 query: each word quoted, OR'd, so BM25 does the ranking."""
    words = re.findall(r"[\w$.,%-]+", query.lower())
    words = [w.strip(".,") for w in words if w.strip(".,")]
    return " OR ".join(f'"{w}"' for w in words)


def search(con, query: str, ein: str | None = None, tax_year: int | None = None,
           k: int = 5) -> list[dict]:
    m = _match(query)
    if not m:
        return []
    sql = ("SELECT s.id, s.ein, s.tax_year, s.form, s.title, s.text, "
           "bm25(sections_fts, 2.0, 1.0) AS score "
           "FROM sections_fts JOIN sections s ON s.rowid = sections_fts.rowid "
           "WHERE sections_fts MATCH ?")
    args: list = [m]
    if ein:
        sql += " AND s.ein = ?"
        args.append(ein)
    if tax_year:
        sql += " AND s.tax_year = ?"
        args.append(tax_year)
    sql += " ORDER BY score LIMIT ?"
    args.append(k)
    return [dict(r) for r in con.execute(sql, args)]


def find_org(con, name: str, k: int = 5) -> list[dict]:
    m = _match(name)
    if not m:
        return []
    rows = con.execute("SELECT o.ein, o.name, o.ntee, bm25(orgs_fts) AS score FROM orgs_fts "
                       "JOIN orgs o ON o.ein = orgs_fts.ein WHERE orgs_fts MATCH ? "
                       "ORDER BY score LIMIT ?", (m, k)).fetchall()
    out = []
    for r in rows:
        years = con.execute("SELECT tax_year, form FROM filings WHERE ein = ? "
                            "ORDER BY tax_year DESC", (r["ein"],)).fetchall()
        out.append({"ein": r["ein"], "name": r["name"], "ntee": r["ntee"],
                    "filings": [f"{y['tax_year']} {y['form']}" for y in years]})
    return out


def get_section(con, section_id: str) -> dict | None:
    r = con.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
    return dict(r) if r else None


def get_facts(con, ein: str, tax_year: int | None = None) -> dict | None:
    sql, args = "SELECT * FROM filings WHERE ein = ?", [ein]
    if tax_year:
        sql += " AND tax_year = ?"
        args.append(tax_year)
    r = con.execute(sql + " ORDER BY tax_year DESC LIMIT 1", args).fetchone()
    return {**dict(r), "facts": json.loads(r["facts"])} if r else None
