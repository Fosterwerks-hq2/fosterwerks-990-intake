"""Smoke test: launch the MCP server over stdio like a real client and exercise every tool.

  .venv/bin/python tests/test_mcp_server.py

Uses a throwaway review database so the real queue is untouched. No API calls.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
AMP = "010559608"


def payload(result):
    """Structured content if the tool returned it, else the text."""
    if getattr(result, "structured_content", None) is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return "\n".join(c.text for c in result.content if c.type == "text")


async def main():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "INTAKE_CLIENT": "smoke-test", "INTAKE_REVIEW_DB": f"{tmp}/review.sqlite"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "intake.mcp_server"],
                                   cwd=ROOT, env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        print("server:", init.server_info.name, init.server_info.version)
        tools = (await s.list_tools()).tools
        print("tools:", [t.name for t in tools])
        assert {t.name for t in tools} == {"find_org", "search_filings", "get_section",
                                          "propose_prospect", "list_review_queue"}

        orgs = payload(await s.call_tool("find_org", {"name": "allied media"}))
        print("find_org:", orgs[0]["ein"], orgs[0]["name"], orgs[0]["filings"])
        assert orgs[0]["ein"] == AMP

        hits = payload(await s.call_tool("search_filings", {"query": "total revenue employees",
                                                            "ein": AMP, "tax_year": 2023}))
        print("search_filings:", [h["id"] for h in hits])
        p1 = payload(await s.call_tool("get_section", {"section_id": f"{AMP}/2023/990/p1"}))
        assert "total employee: 108" in p1["text"] and "$24,072,145" in p1["text"]

        bad = await s.call_tool("get_section", {"section_id": "nope"})
        print("get_section bad id -> isError:", bad.is_error)
        assert bad.is_error

        record = {
            "ein": AMP, "name": "ALLIED MEDIA PROJECTS INC", "tax_year": 2023, "form": "990",
            "mission": "Media strategies for a more just, creative and collaborative world.",
            "total_revenue": 24072145, "total_expenses": 33120543, "employees": 108,
            "programs": ["Allied Media Conference", "Sponsored projects (fiscal sponsorship)"],
            "sensitive_data": [], "fit_notes": "Large fiscal sponsor with many projects.",
            "citations": [{"claim": "revenue, expenses, employees",
                           "section_id": f"{AMP}/2023/990/p1"},
                          {"claim": "programs", "section_id": f"{AMP}/2023/990/p3"}],
        }
        wrong = {**record, "total_revenue": 25000000}
        res = await s.call_tool("propose_prospect", {"record": wrong})
        print("propose with an unsupported figure -> isError:", res.is_error, "|",
              payload(res).splitlines()[-1] if res.is_error else "")
        assert res.is_error

        other = {**record, "citations": [{"claim": "x", "section_id": hits[0]["id"].replace(AMP, "262518642")}]}
        res = await s.call_tool("propose_prospect", {"record": other})
        assert res.is_error

        ok = payload(await s.call_tool("propose_prospect", {"record": record}))
        print("propose ok -> review item", ok["id"], ok["status"], "by", ok["proposed_by"])
        assert ok["status"] == "pending" and ok["proposed_by"] == "smoke-test"

        q = payload(await s.call_tool("list_review_queue", {}))
        assert [i["id"] for i in q] == [ok["id"]]
    print("all checks passed")


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
