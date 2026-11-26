"""The `live` adapter: asks an Anthropic model each Case's question, opt-in and bounded (`docs/design.md`, section 5).

It needs `--live`, a key in `AGENTKIT_ANTHROPIC_API_KEY` and a price for its model in `budget.json`, and checks the ceiling of the invocation before each call. Tests replace `AnthropicClient`; none holds a real key."""

import json
import math
import re
import time
import urllib.request
from dataclasses import dataclass

from agentkit.adapters.scripted import Answer
from agentkit.budget import Ceiling, CeilingReached, Price
from agentkit.cases import Case, WorkloadError

NAME = "live"
ORIGIN = "external_run"
KEY_VARIABLE = "AGENTKIT_ANTHROPIC_API_KEY"
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL_ID = re.compile(r"claude-[a-z0-9][a-z0-9.-]*")
MAX_OUTPUT_TOKENS = 1024
# Below this many output tokens a call is not made: the answer would be cut before it says anything.
MIN_OUTPUT_TOKENS = 16
# Added to the prompt's UTF-8 length to bound the input tokens of a call before it is made: the message framing.
FRAMING_TOKENS = 16


class LiveRefused(WorkloadError):
    """The live adapter was asked for without `--live`, a key, a model id or a price: no call is made."""


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class AnthropicClient:
    """The Messages API through the standard library. Only `LiveAdapter.answer` calls it."""

    def __init__(self, key: str):
        self._key = key

    def complete(self, model: str, prompt: str, max_tokens: int) -> Completion:
        body = json.dumps({"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        request = urllib.request.Request(
            MESSAGES_URL,
            data=body,
            method="POST",
            headers={"x-api-key": self._key, "anthropic-version": API_VERSION, "content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            reply = json.loads(response.read().decode("utf-8"))
        text = "".join(block.get("text", "") for block in reply["content"] if block.get("type") == "text")
        return Completion(text, reply["usage"]["input_tokens"], reply["usage"]["output_tokens"])


def build(*, live: bool, model: str | None, environ, ceiling: Ceiling, price_of) -> "LiveAdapter":
    """The adapter, or `LiveRefused` before any call. `price_of(model)` raises when the budget holds no price."""
    if not live:
        raise LiveRefused("the live adapter calls a paid model: it needs --live")
    if not model or not MODEL_ID.fullmatch(model):
        raise LiveRefused("the live adapter needs an explicit Anthropic model id (--model claude-...); there is no default")
    key = environ.get(KEY_VARIABLE)
    if not key:
        raise LiveRefused(f"the live adapter needs its key in {KEY_VARIABLE}")
    return LiveAdapter(model, ceiling, price_of(model), AnthropicClient(key))


class LiveAdapter:
    name = NAME
    is_live = True

    def __init__(self, model: str, ceiling: Ceiling, price: Price, client):
        self.settings = {"model": model}
        self._model, self._ceiling, self._price, self._client = model, ceiling, price, client
        self.calls = 0
        self.tokens = 0
        self.usd = 0.0

    def _output_bound(self, prompt: str) -> int:
        """The output tokens the next call may use within the ceiling; raises `CeilingReached` when that is too few."""
        if self.calls >= self._ceiling.calls:
            raise CeilingReached("call_ceiling")
        input_bound = len(prompt.encode("utf-8")) + FRAMING_TOKENS
        by_tokens = self._ceiling.tokens - self.tokens - input_bound
        if by_tokens < MIN_OUTPUT_TOKENS:
            raise CeilingReached("token_ceiling")
        left = self._ceiling.usd - self.usd - input_bound * self._price.input_usd_per_mtok / 1e6
        by_cost = math.floor(left * 1e6 / self._price.output_usd_per_mtok) if self._price.output_usd_per_mtok else MAX_OUTPUT_TOKENS
        if by_cost < MIN_OUTPUT_TOKENS:
            raise CeilingReached("cost_ceiling")
        return min(MAX_OUTPUT_TOKENS, by_tokens, by_cost)

    def answer(self, case: Case, repeat: int) -> Answer:
        max_tokens = self._output_bound(case.question)
        self.calls += 1
        started = time.monotonic()
        completion = self._client.complete(self._model, case.question, max_tokens)
        latency = time.monotonic() - started
        tokens = completion.input_tokens + completion.output_tokens
        cost = (completion.input_tokens * self._price.input_usd_per_mtok + completion.output_tokens * self._price.output_usd_per_mtok) / 1e6
        self.tokens += tokens
        self.usd += cost
        return Answer(fields={
            "observation_status": "produced",
            "raw_answer": completion.text,
            "usage": {self._model: {"tokens": tokens, "latency_seconds": latency, "cost_usd": cost}},
        })
