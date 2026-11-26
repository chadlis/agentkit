"""The `live` adapter (`docs/design.md`, section 5), tested with a fake client only: its provider, `live.AnthropicClient`, is replaced in every test, and no test holds a real key. Offline; no model call."""

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentkit.adapters import live
from agentkit.cli import main
from agentkit.ledger import read_records
from test_budget import write_budget

MODEL = "claude-fake-model"
KEY_VARIABLE = "AGENTKIT_ANTHROPIC_API_KEY"
PRICE = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 5.0, "as_of": "2026-11-26", "source": "a pricing page, for the test"}


class FakeClient:
    """Stands for the Anthropic client: answers every call with the same text and token counts, and counts its calls."""

    calls: list[dict] = []

    def __init__(self, key: str):
        self.key = key

    def complete(self, model: str, prompt: str, max_tokens: int):
        FakeClient.calls.append({"model": model, "prompt": prompt, "max_tokens": max_tokens})
        return SimpleNamespace(text=f"an answer to: {prompt}", input_tokens=10, output_tokens=20)


@pytest.fixture
def fake_client(monkeypatch) -> list[dict]:
    FakeClient.calls = []
    monkeypatch.setattr(live, "AnthropicClient", FakeClient, raising=False)
    return FakeClient.calls


@pytest.fixture
def five_cases(tmp_path) -> Path:
    """A Workload of 5 dev Cases, written by the test."""
    directory = tmp_path / "five"
    directory.mkdir()
    (directory / "workload.json").write_text(json.dumps({"cases": {"dev": "cases-dev.jsonl"}}), encoding="utf-8")
    rows = [{"case_id": f"C{number}", "question": f"Question {number}?", "group": "g", "family": "f"} for number in range(1, 6)]
    (directory / "cases-dev.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return directory


def live_run(workload_dir: Path, *flags: str) -> int:
    return main(["run", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "live", "--model", MODEL, *flags])


def test_the_live_adapter_without_the_live_flag_or_without_the_key_is_refused_before_any_call(five_cases, fake_client, monkeypatch):
    write_budget(live={"calls": 5, "tokens": 100000, "usd": 10}, prices={MODEL: PRICE})
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    without_flag = live_run(five_cases)
    monkeypatch.delenv(KEY_VARIABLE)
    without_key = live_run(five_cases, "--live")
    assert (without_flag, without_key, len(fake_client), read_records()) == (2, 2, 0, [])


def test_a_ceiling_of_3_calls_on_5_cases_makes_exactly_3_calls_and_exports_3_produced_and_2_missing_lines(five_cases, fake_client, monkeypatch, tmp_path):
    write_budget(live={"calls": 3, "tokens": 100000, "usd": 10}, prices={MODEL: PRICE})
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    assert live_run(five_cases, "--live") == 8
    assert len(fake_client) == 3
    [record] = read_records()
    assert (record["status"], record["reason"]) == ("failed", "call_ceiling")
    out = tmp_path / "out"
    assert main(["export", "--run", record["run_id"], "--out", str(out)]) == 0
    lines = [json.loads(text) for text in (out / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    assert Counter(line["observation_status"] for line in lines) == {"produced": 3, "missing": 2}
    assert [line["metadata"] for line in lines if line["observation_status"] == "missing"] == [{"reason": "call_ceiling"}] * 2
    assert {line["origin"] for line in lines} == {"external_run"}


def test_a_spend_ceiling_bounds_the_output_tokens_of_a_call_and_stops_the_run_with_cost_ceiling(five_cases, fake_client, monkeypatch):
    # Each fake call costs 10 x 1 + 20 x 5 = 110 USD per million tokens: 0.00011 USD.
    write_budget(live={"calls": 5, "tokens": 100000, "usd": 0.00025}, prices={MODEL: PRICE})
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    assert live_run(five_cases, "--live") == 8
    [record] = read_records()
    assert (record["status"], record["reason"]) == ("failed", "cost_ceiling")
    assert [call["max_tokens"] * PRICE["output_usd_per_mtok"] / 1e6 <= 0.00025 for call in fake_client] == [True] * len(fake_client)
    assert len(fake_client) == 2


def test_a_live_run_without_a_price_for_its_model_is_refused_before_any_call(five_cases, fake_client, monkeypatch):
    write_budget(live={"calls": 5, "tokens": 100000, "usd": 10}, prices={})
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    assert (live_run(five_cases, "--live"), len(fake_client), read_records()) == (2, 0, [])


def test_a_live_run_under_the_versioned_zero_ceiling_exits_8_with_a_refused_record_and_no_call(five_cases, fake_client, monkeypatch):
    write_budget(prices={MODEL: PRICE})
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    assert (live_run(five_cases, "--live"), len(fake_client)) == (8, 0)
    assert [(record["status"], record["reason"]) for record in read_records()] == [("refused", "live")]
