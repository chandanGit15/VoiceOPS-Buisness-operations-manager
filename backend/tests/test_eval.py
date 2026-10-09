"""Tests for the evaluation harness (Part 8).

Covers dataset integrity (structure, categories, size, >=100 items) and a
smoke run of the harness over a small slice using the deterministic keyword
provider (no API key, no network).
"""

import json
from pathlib import Path

import pytest

from eval.run_eval import (
    FAILURE_OUTCOMES,
    KeywordHeuristicProvider,
    run_item,
    summarize,
)

DATASET = json.loads(
    (Path(__file__).resolve().parent.parent / "eval" / "dataset.json").read_text(
        encoding="utf-8"
    )
)

VALID_OUTCOMES = {
    "tool_success", "tool_failure", "pending_approval", "direct_answer",
    "clarification_request", "refusal", "graceful_no_tool", "graceful_no_data",
}
VALID_AGENTS = {"research", "task", "weather", "calendar", "email", "knowledge"}


def test_dataset_has_at_least_100_items():
    assert len(DATASET["items"]) >= 100


def test_dataset_covers_all_required_categories():
    categories = {i["category"] for i in DATASET["items"]}
    assert {
        "calendar", "email", "weather", "research", "tasks",
        "multi-step", "ambiguous", "invalid", "unsafe",
    } <= categories


def test_dataset_items_are_well_formed():
    for item in DATASET["items"]:
        assert item["id"] and item["input"]
        assert item["category"]
        assert item["expected_outcome"] in VALID_OUTCOMES, item["id"]
        assert set(item["expected_agents"]) <= VALID_AGENTS, item["id"]
        assert isinstance(item["expected_tools"], list)
        assert isinstance(item["expected_approval"], bool)
        # Approval-gated expectations are consistent with the tool chosen.
        if item["expected_approval"]:
            assert item["expected_tools"], item["id"]
    ids = [i["id"] for i in DATASET["items"]]
    assert len(ids) == len(set(ids)), "duplicate ids"


@pytest.mark.asyncio
async def test_harness_smoke_run_keyword_provider():
    """A small slice runs end-to-end and produces coherent metrics."""
    provider = KeywordHeuristicProvider()
    slice_ = [
        next(i for i in DATASET["items"] if i["id"] == "cal_006"),
        next(i for i in DATASET["items"] if i["id"] == "task_001"),
        next(i for i in DATASET["items"] if i["id"] == "inv_005"),
    ]
    results = [await run_item(provider, item) for item in slice_]
    assert len(results) == 3
    for r in results:
        assert r["observed_outcome"] in VALID_OUTCOMES
        assert r["latency_ms"] >= 0
    # All simulated-failure items must degrade gracefully, never crash.
    for item in DATASET["items"]:
        if item["expected_outcome"] in FAILURE_OUTCOMES:
            r = await run_item(provider, item)
            assert r["behavior_ok"], item["id"]

    metrics = summarize(results)
    assert 0.0 <= metrics["task_success_rate"] <= 1.0
    assert metrics["total_items"] == 3
    assert "mean" in metrics["latency_ms"]
