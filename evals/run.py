"""Run the 990 Q&A eval through the real agent entry point (intake.agent.Run).

  .venv/bin/python -m evals.run --variant baseline --model claude-opus-5-5 [--only rev-1,ez-1]
  .venv/bin/python -m evals.run --variant v1 --model claude-haiku-5-5

Writes .claude/hillclimb/990-qa/<variant>/results.jsonl (one row per case x rep, written as it
finishes, resumable), traces/<id>_rep<k>.json (full transcript), errors.jsonl (attempts that
produced nothing gradable: API errors after retries, timeouts, budget or turn limits, a model
other than the one requested). Then prints accuracy with a 95% interval, the confusion table,
grounding, cost and latency.
"""
import argparse
import asyncio
import json
import math
import os
import random
import sqlite3
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOW = ROOT / ".claude" / "hillclimb" / "990-qa"
EVAL_DB = ROOT / "data" / "eval.sqlite"

# The agent's MCP server must read the eval copy (with the planted instruction), and nothing
# may touch the real review queue. Set before the agent module reads its environment.
os.environ["INTAKE_DB"] = str(EVAL_DB)
os.environ["INTAKE_REVIEW_DB"] = str(Path(tempfile.mkdtemp()) / "review.sqlite")

from intake.agent import Run  # noqa: E402

from .grade import grade  # noqa: E402

# $ per million tokens (Claude API, first party). Cache writes 1.25x input (5-minute TTL).
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
RETRYABLE = {429, 500, 502, 503, 529}


def cost(model: str, u: dict) -> float:
    p = PRICES[model]
    return (u["input_tokens"] * p["in"] + u["output_tokens"] * p["out"]
            + u["cache_read_input_tokens"] * p["cache_read"]
            + u["cache_creation_input_tokens"] * p["cache_write"]) / 1e6


def usage_for(model: str, out: dict) -> dict:
    """Token counts for the requested model, from the SDK's per-model usage."""
    mu = (out.get("model_usage") or {}).get(model) or {}
    return {"input_tokens": mu.get("inputTokens", 0), "output_tokens": mu.get("outputTokens", 0),
            "cache_read_input_tokens": mu.get("cacheReadInputTokens", 0),
            "cache_creation_input_tokens": mu.get("cacheCreationInputTokens", 0)}


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


class Runner:
    def __init__(self, variant: str, model: str, reps: int, budget: float, timeout: float):
        self.dir = FLOW / variant
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        self.model, self.reps, self.budget, self.timeout = model, reps, budget, timeout
        self.done = set()
        res = self.dir / "results.jsonl"
        if res.exists():
            for line in res.read_text().splitlines():
                r = json.loads(line)
                self.done.add((r["prompt_id"], r["rep"]))
        self.con = sqlite3.connect(EVAL_DB)
        self.con.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()

    async def _append(self, name: str, row: dict):
        async with self.lock:
            with (self.dir / name).open("a") as f:
                f.write(json.dumps(row) + "\n")

    async def one(self, case: dict, rep: int):
        attempts, out, err = 0, None, None
        while attempts < 3:
            attempts += 1
            started = time.monotonic()
            try:
                out = await asyncio.wait_for(Run("never", self.budget, model=self.model)
                                             .run(case["prompt"]), self.timeout)
            except asyncio.TimeoutError:
                out, err = None, {"class": "timeout", "detail": f">{self.timeout}s"}
                break
            except Exception as e:  # noqa: BLE001  harness failure, never a model score
                out, err = None, {"class": "harness_error", "detail": repr(e)[:500]}
                break
            latency = round(time.monotonic() - started, 1)
            status = out.get("api_error_status")
            if status in RETRYABLE:
                err = {"class": "api_error", "detail": f"HTTP {status}"}
                await asyncio.sleep(min(60, 2 ** attempts + random.random() * 3))
                continue
            break
        base = {"prompt_id": case["id"], "rep": rep, "attempts": attempts, "model": self.model}
        if out is not None and not err:
            served = out.get("served_models") or []
            if out.get("api_error_status"):  # before the model check: auth errors report "<synthetic>"
                err = {"class": "api_error", "detail": f"HTTP {out['api_error_status']}: {out.get('result_text')}"}
            elif set(served) - {self.model} or not served:
                err = {"class": "served_model_mismatch", "detail": f"asked {self.model}, got {served}"}
            elif out.get("subtype") in ("error_max_budget_usd", "error_max_turns",
                                        "error_max_structured_output_retries"):
                err = {"class": out["subtype"], "detail": "scaffold limit reached"}
            elif not out.get("report"):
                err = {"class": "no_report", "detail": (out.get("result_text") or "")[:300]}
        if err:
            u = usage_for(self.model, out) if out else None
            await self._append("errors.jsonl", {**base, **err, "usage": u,
                                                "cost_usd": cost(self.model, u) if u else None})
            print(f"  ERROR {case['id']} rep{rep}: {err['class']} {err['detail'][:100]}")
            return
        rep_ = out["report"]
        g = grade(case, rep_, self.con)
        u = usage_for(self.model, out)
        trace_path = self.dir / "traces" / f"{case['id']}_rep{rep}.json"
        trace_path.write_text(json.dumps(out["trace"] + [
            {"role": "assistant", "content": "RunReport:\n" + json.dumps(rep_, indent=2)}], indent=1))
        tool_calls = sum(1 for t in out["trace"] if t["role"] == "tool_call"
                         and t["name"] != "StructuredOutput")
        row = {**base, "prompt": case["prompt"], "tags": case["tags"],
               "stop_reason": out.get("stop_reason"), "status": "ok",
               "grade": g["grade"], "explanation": g["explanation"], "outcome": g["outcome"],
               "usage": u, "cost_usd": round(cost(self.model, u), 5),
               "sdk_cost_usd": out.get("cost_usd"), "latency_s": out.get("seconds"),
               "tool_calls": tool_calls, "turns": out.get("turns"),
               "injection_flagged": rep_.get("instructions_in_filings"),
               "meta": {"session": out.get("session_id"), "log": out.get("log"),
                        "expected": case["expected"].get("value")}}
        await self._append("results.jsonl", row)
        mark = "ok " if g["grade"]["correct"] else "MISS"
        print(f"  {mark} {case['id']:12} {g['outcome']:16} ${row['cost_usd']:.4f} "
              f"{row['latency_s']}s {tool_calls} tools")

    async def run(self, cases: list[dict], concurrency: int):
        sem = asyncio.Semaphore(concurrency)

        async def guarded(c, r):
            async with sem:
                await self.one(c, r)
        todo = [(c, r) for c in cases for r in range(self.reps) if (c["id"], r) not in self.done]
        print(f"{self.dir.relative_to(ROOT)}: {len(todo)} to run "
              f"({len(cases) * self.reps - len(todo)} already done), model {self.model}")
        await asyncio.gather(*(guarded(c, r) for c, r in todo))


def summarize(variant_dir: Path) -> dict:
    res = variant_dir / "results.jsonl"
    rows = [json.loads(line) for line in res.read_text().splitlines()] if res.exists() else []
    errs = ((variant_dir / "errors.jsonl").read_text().splitlines()
            if (variant_dir / "errors.jsonl").exists() else [])
    n, k = len(rows), sum(r["grade"]["correct"] for r in rows)
    lo, hi = wilson(k, n)
    ans = [r for r in rows if r["tags"][0] != "not-found"]
    neg = [r for r in rows if r["tags"][0] == "not-found"]
    lat = sorted(r["latency_s"] for r in rows)
    if not rows:
        return {"variant": variant_dir.name, "cases": 0, "errors": len(errs)}
    s = {
        "variant": variant_dir.name, "model": rows[0]["model"] if rows else None,
        "cases": n, "errors": len(errs),
        "accuracy": round(k / n, 3) if n else None, "ci95": [round(lo, 3), round(hi, 3)],
        "answerable_correct": f"{sum(r['grade']['correct'] for r in ans)}/{len(ans)}",
        "refused_when_should": f"{sum(r['grade']['correct'] for r in neg)}/{len(neg)}",
        "grounded_on_answerable": f"{sum(r['grade']['grounded'] for r in ans)}/{len(ans)}",
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "by_tag": {t: f"{sum(r['grade']['correct'] for r in rows if r['tags'][1] == t)}/"
                      f"{sum(1 for r in rows if r['tags'][1] == t)}"
                   for t in dict.fromkeys(r["tags"][1] for r in rows)},
        "injection_flagged": f"{sum(1 for r in rows if 'injection' in r['tags'] and r['injection_flagged'])}/"
                             f"{sum(1 for r in rows if 'injection' in r['tags'])}",
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 4),
        "cost_usd_per_case": round(sum(r["cost_usd"] for r in rows) / n, 4) if n else None,
        "sdk_cost_estimate_total": round(sum(r["sdk_cost_usd"] or 0 for r in rows), 4),
        "latency_s_median": lat[len(lat) // 2] if lat else None,
        "tool_calls_mean": round(sum(r["tool_calls"] for r in rows) / n, 2) if n else None,
    }
    return s


def main():
    ap = argparse.ArgumentParser(prog="python -m evals.run")
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--model", default="claude-opus-5-5", choices=sorted(PRICES))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--budget", type=float, default=0.25, help="max USD per case")
    ap.add_argument("--timeout", type=float, default=240, help="seconds per case, hard ceiling")
    ap.add_argument("--only", help="comma-separated case IDs (pilot)")
    ap.add_argument("--summary-only", action="store_true")
    a = ap.parse_args()
    cases = [json.loads(line) for line in (ROOT / "evals" / "cases.jsonl").read_text().splitlines()]
    if a.only:
        keep = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    FLOW.mkdir(parents=True, exist_ok=True)
    state = FLOW / "_state.json"
    if not state.exists():
        state.write_text(json.dumps({
            "flow": "990-qa",
            "metrics": [{"id": "correct", "label": "Correct", "kind": "binary"},
                        {"id": "grounded", "label": "Grounded", "kind": "binary"}],
            "perf_fields": ["cost_usd", "latency_s", "tool_calls"],
        }, indent=2))
    if not a.summary_only:
        t = time.monotonic()
        asyncio.run(Runner(a.variant, a.model, a.reps, a.budget, a.timeout).run(cases, a.concurrency))
        print(f"wall clock {time.monotonic() - t:.0f}s")
    print(json.dumps(summarize(FLOW / a.variant), indent=2))


if __name__ == "__main__":
    main()
