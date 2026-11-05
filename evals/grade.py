"""Grade one RunReport against one case. Programmatic only: no model judge.

Metrics (both on every case, 0 or 1):
  correct   answerable case: the figure is within 1% of the IRS answer key (rounded answers
            like "$24.1 million" pass; outcome "correct" vs "correct_rounded" says which),
            yes-no or name matches
            not-answerable case: the agent said not found (found=false) instead of answering
  grounded  answerable case: at least one citation is a section of the right organization
            whose text contains the answer
            not-answerable case: no citation points to a section that doesn't exist

Outcome class (for the confusion table):
  answerable:      correct | correct_rounded | wrong | false_not_found
  not answerable:  correct_refusal | hallucinated

A missing report (crash, timeout, budget stop) is never graded here; the runner logs it as an
error, so "no answer" is never confused with "said not found".
"""
import re
import sqlite3

YEAR = re.compile(r"^(19|20)\d\d$")


SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mm": 1e6, "billion": 1e9, "b": 1e9,
         "bn": 1e9}
TOLERANCE = 0.01  # Evan, 2026-11-05: accept rounded figures within 1% ("$24.1 million")


def _numbers(text: str) -> list[float]:
    out = []
    for m in re.finditer(r"(\$)?(-?\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|bn|mm|k|m|b)?\b",
                         text or "", re.I):
        raw = m.group(2).replace(",", "")
        try:
            n = float(raw)
        except ValueError:
            continue
        if not m.group(1) and not m.group(3) and YEAR.match(raw):  # "tax year 2023" isn't it
            continue
        out.append(n * SCALE.get((m.group(3) or "").lower(), 1))
    return out


def _close(n: float, gold: int) -> bool:
    return n == gold if gold == 0 else abs(n - gold) <= TOLERANCE * abs(gold)


def _yesno(text: str) -> str | None:
    m = re.search(r"\b(yes|no)\b", (text or "").lower())
    return m.group(1).capitalize() if m else None


def _section(con, sid: str):
    return con.execute("SELECT ein, text FROM sections WHERE id = ?", (sid,)).fetchone()


def answer_text(case: dict, report: dict) -> str:
    """The value field is where the figure belongs; fall back to the prose answer if the agent
    left value empty, so a right answer in the wrong field isn't scored as wrong."""
    return report.get("value") or report.get("answer") or ""


def is_right(case: dict, report: dict) -> bool:
    exp = case["expected"]
    text = answer_text(case, report)
    if exp["kind"] == "number":
        nums = _numbers(text)
        return bool(nums) and _close(nums[0], exp["value"])
    if exp["kind"] == "yesno":
        return _yesno(text) == exp["value"]
    if exp["kind"] == "name":
        tokens = [t for t in re.findall(r"[A-Z]+", exp["value"].upper()) if len(t) > 1]
        return all(t in text.upper() for t in tokens)
    raise ValueError(exp["kind"])


def is_exact(case: dict, report: dict) -> bool:
    """Stricter than `correct`: the figure exactly as filed (reported alongside, not graded)."""
    exp = case["expected"]
    if exp["kind"] != "number":
        return is_right(case, report)
    nums = _numbers(answer_text(case, report))
    return bool(nums) and nums[0] == exp["value"]


def evidence_needle(case: dict) -> str:
    exp = case["expected"]
    if exp["kind"] == "number":
        return f"total employee: {exp['value']}" if "employees" in case["tags"] else f"${exp['value']:,}"
    if exp["kind"] == "yesno":
        return f"audited: {exp['value']}"
    return exp["value"]


def grade(case: dict, report: dict, con: sqlite3.Connection) -> dict:
    exp = case["expected"]
    cites = [c.get("section_id", "") for c in report.get("citations") or []]
    sections = {sid: _section(con, sid) for sid in cites}
    missing = [sid for sid, s in sections.items() if s is None]

    if exp["kind"] == "not_found":
        refused = report.get("found") is False
        correct = refused
        outcome = "correct_refusal" if refused else "hallucinated"
        grounded = not missing
        why = ("said not found" if refused else f"answered: {answer_text(case, report)[:120]!r}")
    else:
        if report.get("found") is False:
            correct, outcome = False, "false_not_found"
        else:
            correct = is_right(case, report)
            outcome = ("correct" if is_exact(case, report) else "correct_rounded") if correct else "wrong"
        needle = evidence_needle(case)
        grounded = any(s is not None and s["ein"] == exp["ein"] and needle in s["text"]
                       for s in sections.values())
        why = f"expected {exp['value']!r}, got {answer_text(case, report)[:120]!r}"
    if missing:
        why += f"; cited sections that don't exist: {missing}"
    return {"grade": {"correct": int(correct), "grounded": int(grounded)},
            "outcome": outcome,
            "explanation": {"correct": why,
                            "grounded": "citations: " + (", ".join(cites) or "none")}}


def oracle_report(case: dict, con: sqlite3.Connection) -> dict:
    """What a perfect agent would return: used to prove the grader passes right answers."""
    exp = case["expected"]
    if exp["kind"] == "not_found":
        return {"found": False, "answer": "Not in the filings.", "value": None, "citations": []}
    like = f"{exp['ein']}/{exp['tax_year']}/{exp['form']}/{exp['evidence']['part']}%"
    needle = evidence_needle(case)
    sid = next(r["id"] for r in con.execute("SELECT id, text FROM sections WHERE id LIKE ?", (like,))
               if needle in r["text"])
    value = (f"${exp['value']:,}" if exp["kind"] == "number" and "employees" not in case["tags"]
             else str(exp["value"]))
    return {"found": True, "answer": value, "value": value,
            "citations": [{"claim": "answer", "section_id": sid}]}
