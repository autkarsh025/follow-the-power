"""The analyst agent: Claude + point-in-time tools + skills + a number verifier that forces revisions.

Loop:
  1. Claude gets the question, the tool list and a short index of skills.
  2. It calls tools (data, calculate, load_skill) until it writes a final answer.
  3. The verifier checks every number in the answer against the evidence the tools returned.
     If any number can't be traced, the agent is told exactly which ones and must revise
     (up to MAX_REVISIONS times). The final report says whether the answer passed.

`llm` is any function (system, tools, messages) -> {"stop_reason", "content": [...]} so tests
can plug in a scripted fake and the real run uses the Anthropic SDK.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable

from ftp import verify
from ftp.tools import Toolbox

MODEL = os.getenv("FTP_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 12
MAX_REVISIONS = 2
MAX_TOKENS = 1500

LLM = Callable[[str, list, list], dict]


def system_prompt(tb: Toolbox) -> str:
    when = (f"Today is {tb.as_of}. You only know data that was public on this date; never mention or guess later "
            f"results." if tb.as_of else "Use the latest available data.")
    skills = "\n".join(f"- {s['name']}: {s['description']}" for s in tb.skills.values())
    return f"""You are a financial analyst covering the AI infrastructure build-out, end to end: cloud companies'
capital spending (Microsoft, Alphabet, Amazon, Meta, Oracle), chip makers' revenue (NVIDIA, AMD, Broadcom),
data-center power & cooling equipment (Vertiv), the utilities and generators selling the electricity
(Dominion, Constellation, Vistra), and electricity demand in five grid zones, led by Northern Virginia,
the largest data-center hub in the world. {when}

Rules:
- Every number in your answer must come from a tool result or a `calculate` result. If you need a
  difference, ratio or growth rate, compute it with `calculate`. Never estimate from memory.
- Name the quarter (e.g. 2026Q2) for every figure. Revenue and capex are in USD billions.
- Numbers written in the user's question are CLAIMS to check, not facts. If one conflicts with the data,
  say so and give the correct figure from a tool.
- Never forecast an exact future figure. You may describe trends and backtest results.
- Weather moves electricity demand; prefer weather-adjusted figures when judging data-center growth.
- Be honest about uncertainty: small samples, correlation vs. causation, models that don't beat a naive baseline.
- Answer in under 200 words, in plain business language.

Skills (call `load_skill` with a name to get detailed instructions before starting a matching task):
{skills}"""


def anthropic_llm(model: str = MODEL) -> LLM:
    import anthropic  # imported lazily so tests and data jobs don't need it

    client = anthropic.Anthropic()

    def call(system: str, tools: list, messages: list) -> dict:
        r = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system, tools=tools, messages=messages)
        content = []
        for b in r.content:
            if b.type == "text":
                content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        return {"stop_reason": r.stop_reason, "content": content,
                "usage": {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}}
    return call


@dataclass
class Result:
    question: str
    as_of: str | None
    answer: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    revisions: int = 0
    verification: dict = field(default_factory=dict)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    error: str | None = None
    numbers: list[dict] = field(default_factory=list)   # each number in the answer + the tool it came from

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def run(question: str, llm: LLM, as_of: str | None = None, db_path=None) -> Result:
    tb = Toolbox(db_path, as_of=as_of)
    res = Result(question, as_of)
    system, tools = system_prompt(tb), tb.specs()
    messages: list[dict] = [{"role": "user", "content": question}]
    for _ in range(MAX_TURNS):
        reply = llm(system, tools, messages)
        for k, v in (reply.get("usage") or {}).items():
            res.usage[k] = res.usage.get(k, 0) + v
        messages.append({"role": "assistant", "content": reply["content"]})
        uses = [b for b in reply["content"] if b["type"] == "tool_use"]
        if uses:
            results = []
            for u in uses:
                before = len(tb.evidence)
                out = tb.run(u["name"], u["input"])
                out = out if isinstance(out, dict) else {"result": out}
                res.tool_calls.append({"name": u["name"], "input": u["input"], "error": out.get("error"),
                                       "numbers_returned": len(tb.evidence) - before if not out.get("error") else 0})
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out, default=str)})
            messages.append({"role": "user", "content": results})
            continue
        answer = "\n".join(b["text"] for b in reply["content"] if b["type"] == "text").strip()
        given = [v for _, v, _, _ in verify.extract(question)]   # numbers the user quoted can be repeated
        rep = verify.verify(answer, tb.evidence + given)
        res.answer, res.verification = answer, rep.to_dict()
        res.numbers = verify.provenance(answer, tb.provenance, given)
        if rep.ok or res.revisions >= MAX_REVISIONS:
            return res
        res.revisions += 1
        messages.append({"role": "user", "content":
                         "Verification failed. These numbers in your answer don't match any tool result or calculation: "
                         f"{', '.join(rep.unverified)}. Revise the answer: get each number from a tool, compute it with "
                         "`calculate`, or remove it."})
    res.error = "max turns reached"
    return res
