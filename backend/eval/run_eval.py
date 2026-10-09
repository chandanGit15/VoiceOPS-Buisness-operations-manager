"""Agent evaluation harness (Part 8).

Runs the real supervisor graph over a labeled dataset (eval/dataset.json)
and measures routing accuracy, tool selection accuracy, failure handling,
latency, and approval-gating frequency.

Providers:
  keyword (default) — deterministic generic-keyword router. No API key
    needed; measures the harness + orchestration mechanics. Accuracy here is
    NOT a claim about LLM quality.
  real — uses the configured LLM provider (GEMINI_API_KEY required) for
    routing/planning decisions.

Tool execution uses stub wrappers of the REAL tool classes (same names and
argument schemas) so no external credentials are needed:
  - approval-gated tools return the same awaiting_approval envelope the real
    ApprovalGateTool produces before a human confirms;
  - items labeled tool_failure get a simulated tool failure — the metric
    measures graceful handling, not real API failure rates;
  - everything else returns canned success.

Usage (from backend/):
  python -m eval.run_eval                    # keyword provider
  python -m eval.run_eval --provider real    # live LLM
  python -m eval.run_eval --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path

from app.agents.graph import build_graph
from app.tools.base import Tool, ToolError, ToolTrace
from app.tools.registry import AGENT_TOOLS

logging.disable(logging.WARNING)  # keep eval output clean

DATASET_PATH = Path(__file__).with_name("dataset.json")

# Categories whose expected outcome is a *simulated* tool failure: the stub
# raises ToolError so we can measure that the graph degrades gracefully.
FAILURE_OUTCOMES = {"tool_failure"}


def cal_tool(low: str) -> str:
    """Pick the calendar tool for a phrase (generic keyword logic)."""
    if any(w in low for w in ("cancel", "remove", "delete")):
        return "delete_event"
    if any(w in low for w in ("move", "reschedule", "push", "update", "shorter")):
        return "update_event"
    if any(w in low for w in ("add", "book", "create", "schedule a", "set up",
                              "lunch with", "book a")):
        return "create_event"
    if any(w in low for w in ("free", "available", "availability")):
        return "check_availability"
    return "get_events"


def mail_tool(low: str) -> str:
    """Pick the email tool for a phrase (generic keyword logic)."""
    if "send" in low:
        return "send_email"
    if any(w in low for w in ("draft", "write", "compose")):
        return "draft_email"
    if any(w in low for w in ("read", "open")):
        return "read_email"
    return "search_emails"


def task_tool(low: str) -> str:
    """Pick the task tool for a phrase (generic keyword logic)."""
    if any(w in low for w in ("complete", "mark", "check off", "done")):
        return "complete_task"
    if any(w in low for w in ("list", "show me my", "to-do list", "what's on my")):
        return "list_tasks"
    return "create_task"# ---------- Deterministic keyword-heuristic provider ----------

# Lives in app.services.llm.keyword_provider so both the eval harness and
# the app factory (LLM_PROVIDER=keyword) use the identical heuristic.
from app.services.llm.keyword_provider import KeywordHeuristicProvider  # noqa: E402

# ---------- Stub tool layer (real names/schemas, no credentials) ----------


class StubTool(Tool):
    """Wraps a real tool class: same name + Args validation, canned result."""

    needs_approval = False
    _fail = False
    _captured: list[dict] = []  # replaced per-run by stub_tools_for

    class Args(Tool.Args):
        pass

    async def _run(self, args):
        self._captured.append({"tool": self.name, "awaiting_approval": self.needs_approval})
        if self._fail:
            raise ToolError("Simulated upstream failure (eval)")
        if self.needs_approval:
            return {"awaiting_approval": True, "approval_id": "eval",
                    "risk_level": "medium", "summary": "eval", "message": "needs approval"}
        return {"status": "ok", "eval_stub": True}


def stub_tools_for(expected_outcome: str, captured: list[dict], *, permissive_args: bool = False) -> dict[str, dict[str, type[Tool]]]:
    """Build the injectable tool map: real Args validation, stub execution.

    permissive_args=True swaps in a catch-all Args model — used with the
    keyword provider, which does not extract real arguments. The REAL tool
    argument schemas are enforced when provider=real generates them.
    """
    fail = expected_outcome in FAILURE_OUTCOMES
    if permissive_args:
        from pydantic import ConfigDict

        class PermissiveArgs(Tool.Args):
            model_config = ConfigDict(extra="allow")

    tool_map: dict[str, dict[str, type[Tool]]] = {}
    for agent, tools in AGENT_TOOLS.items():
        stubs: dict[str, type[Tool]] = {}
        for name, real_cls in tools.items():
            approval = hasattr(real_cls, "_execute") and real_cls.__name__.endswith(
                ("EventTool", "EmailTool")) and name in (
                "create_event", "update_event", "delete_event", "send_email",
            )
            stub = type(f"Stub{name}", (StubTool,), {
                "name": name,
                "description": real_cls.description,
                "agent": real_cls.agent,
                "Args": PermissiveArgs if permissive_args else real_cls.Args,
                "needs_approval": approval,
                "_fail": fail,
                "_captured": captured,
            })
            stubs[name] = stub
        tool_map[agent] = stubs
    return tool_map


# ---------- Outcome classification ----------


def classify(result_agents: list[str], trace: list[dict], awaiting_approval: bool) -> str:
    tool_events = [e for e in trace if e.get("action") == "tool_call" and e.get("tool")]
    errors = [e for e in tool_events if e.get("status") == "error"]
    if awaiting_approval:
        return "pending_approval"
    if not result_agents and not tool_events:
        return "direct_answer"  # no delegation: clarification/refusal/direct
    if errors:
        return "tool_failure"
    if tool_events:
        return "tool_success"
    return "direct_answer"


# Buckets: several expected outcomes share one observable behavior class.
BEHAVIOR_BUCKETS = {
    "direct_answer": "no_tool_response",
    "clarification_request": "no_tool_response",
    "refusal": "no_tool_response",
    "graceful_no_tool": "no_tool_response",
    "tool_success": "tool_executed",
    "tool_failure": "tool_executed",
    "pending_approval": "tool_executed",
    "graceful_no_data": "tool_executed",
}


def observed_to_bucket(observed: str) -> str:
    return BEHAVIOR_BUCKETS.get(observed, observed)


async def run_item(provider, item: dict) -> dict:
    """Run one dataset item through the graph; return per-item metrics."""
    trace = ToolTrace()
    captured: list[dict] = []
    graph = build_graph(provider, trace, tool_classes_by_agent=stub_tools_for(
        item["expected_outcome"], captured,
        permissive_args=getattr(provider, "name", "") == "keyword-heuristic",
    ))
    started = time.perf_counter()
    final_state = await graph.ainvoke({
        "user_message": item["input"], "history": [], "trace": [],
        "tool_results": {}, "route": [], "agents_used": [],
        "final_response": "", "step": 0,
    })
    latency_ms = (time.perf_counter() - started) * 1000

    trace_list = final_state.get("trace", [])
    agents_used = sorted(set(final_state.get("agents_used", [])))
    tool_events = [e for e in trace_list if e.get("action") == "tool_call" and e.get("tool")]

    expected_agents = sorted(set(item["expected_agents"]))
    expected_tools = sorted(set(item["expected_tools"]))
    used_tools = sorted({e["tool"] for e in tool_events})

    # Awaiting-approval detection comes from the stub capture list (mirrors
    # ApprovalGateTool returning the envelope instead of executing).
    used_approval_tools = any(c["awaiting_approval"] for c in captured)
    observed = classify(agents_used, trace_list, used_approval_tools)
    routing_ok = agents_used == expected_agents
    tools_ok = used_tools == expected_tools
    behavior_ok = observed_to_bucket(observed) == BEHAVIOR_BUCKETS[item["expected_outcome"]]
    success = behavior_ok and routing_ok and tools_ok

    return {
        "id": item["id"],
        "category": item["category"],
        "observed_outcome": observed,
        "expected_outcome": item["expected_outcome"],
        "behavior_ok": behavior_ok,
        "routing_ok": routing_ok,
        "tools_ok": tools_ok,
        "success": success,
        "used_approval_tool": used_approval_tools,
        "expected_approval": item["expected_approval"],
        "latency_ms": round(latency_ms, 1),
    }


def summarize(results: list[dict]) -> dict:
    latencies = [r["latency_ms"] for r in results]
    approvals_expected = sum(1 for r in results if r["expected_approval"])
    approvals_observed = sum(1 for r in results if r["used_approval_tool"])
    approval_correct = sum(1 for r in results if r["expected_approval"] == r["used_approval_tool"])
    return {
        "total_items": len(results),
        "task_success_rate": round(sum(r["success"] for r in results) / len(results), 3),
        "behavior_correct_rate": round(sum(r["behavior_ok"] for r in results) / len(results), 3),
        "routing_accuracy": round(sum(r["routing_ok"] for r in results) / len(results), 3),
        "tool_selection_accuracy": round(sum(r["tools_ok"] for r in results) / len(results), 3),
        "failed_tool_calls": sum(
            1 for r in results if r["observed_outcome"] == "tool_failure"
        ),
        "human_approval_frequency": {
            "expected_approval_items": approvals_expected,
            "observed_approval_items": approvals_observed,
            "approval_gating_correct": approval_correct,
            "approval_gating_accuracy": round(approval_correct / len(results), 3),
        },
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1),
            "median": round(statistics.median(latencies), 1),
            "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1),
            "note": "Orchestration latency only: excludes real LLM and tool "
                    "network latency when using the keyword provider.",
        },
        "by_category": {
            cat: {
                "items": n,
                "success_rate": round(sum(r["success"] for r in results if r["category"] == cat) / n, 3),
            }
            for cat, n in sorted({c: sum(1 for r in results if r["category"] == c)
                                  for c in (r["category"] for r in results)}.items())
        },
    }


async def main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="VoiceOps agent evaluation")
    parser.add_argument("--provider", choices=["keyword", "real"], default="keyword",
                        help="keyword = deterministic heuristic (no API key); real = configured LLM")
    parser.add_argument("--json", dest="json_out", default=None, help="Write the report to a JSON file")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    args = parser.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    items = dataset["items"]

    if args.provider == "real":
        from app.services.llm import get_llm_provider
        provider = get_llm_provider()  # raises if GEMINI_API_KEY missing
    else:
        provider = KeywordHeuristicProvider()

    results = []
    for item in items:
        results.append(await run_item(provider, item))

    report = {"provider": provider.name, "dataset_version": dataset.get("version"),
              "metrics": summarize(results), "items": results}

    print(f"VoiceOps agent evaluation — provider: {provider.name}")
    print(f"items: {len(results)}")
    m = report["metrics"]
    print(f"task success rate:        {m['task_success_rate']:.1%}")
    print(f"behavior-class accuracy:  {m['behavior_correct_rate']:.1%}")
    print(f"routing accuracy:         {m['routing_accuracy']:.1%}")
    print(f"tool selection accuracy:  {m['tool_selection_accuracy']:.1%}")
    print(f"observed failed tool calls: {m['failed_tool_calls']}")
    print(f"approval gating: {m['human_approval_frequency']['approval_gating_correct']}"
          f"/{len(results)} correct")
    print(f"latency mean/median/p95: {m['latency_ms']['mean']}/"
          f"{m['latency_ms']['median']}/{m['latency_ms']['p95']} ms")
    print("\nper category:")
    for cat, row in m["by_category"].items():
        print(f"  {cat:12s} {row['success_rate']:6.1%}  ({row['items']} items)")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport written to {args.json_out}")
    return 0


def main() -> int:
    return asyncio.run(main_async(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
