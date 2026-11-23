"""Agent, tools and verifier, using a scripted fake LLM (no network, no cost)."""
import json

import pytest

from fixtures import load_synthetic
from ftp import agent, config, db, evals, verify
from ftp.tools import Toolbox, safe_eval


@pytest.fixture
def dbp():
    conn = db.connect(); load_synthetic(conn, True); conn.close()
    return config.DB_PATH


# ------------------------------------------------------------------ verifier
def test_extract_skips_years_quarters_and_counts():
    nums = [raw for raw, *_ in verify.extract("In 2026Q2 (Q2 2026) revenue was $96.2B, up 105.9%, over 3 quarters in 2025.")]
    assert nums == ["$96.2B", "105.9%"]


def test_verify_rounding_and_failures():
    ev = [96.221, 105.87, 13493.833]
    assert verify.verify("Revenue $96.2B grew 106% with 13,494 MW overnight.", ev).ok
    rep = verify.verify("Revenue grew 140%.", ev)
    assert not rep.ok and rep.unverified == ["140%"]
    assert verify.verify("Baseload was 13.5 GW.", ev).ok          # GW converted to MW


# ------------------------------------------------------------------ tools
def test_point_in_time_tools_hide_the_future(dbp):
    latest = Toolbox(dbp).t_quarterly_financials(last_n=40)["rows"]
    past = Toolbox(dbp, as_of="2023-06-01").t_quarterly_financials(last_n=40)["rows"]
    assert past[-1]["quarter"] < latest[-1]["quarter"]
    assert all(r["quarter"] <= "2023Q1" for r in past)             # filed quarter-end + 30 days
    assert "sql" not in [s["name"] for s in Toolbox(dbp, as_of="2023-06-01").specs()]


def test_sql_tool_is_read_only(dbp):
    tb = Toolbox(dbp)
    assert "error" in tb.run("sql", {"query": "DELETE FROM fin_quarterly"})
    assert "error" in tb.run("sql", {"query": "SELECT 1; DROP TABLE x"})
    assert tb.run("sql", {"query": "SELECT COUNT(*) AS n FROM fin_quarterly"})["rows"][0]["n"] > 0


def test_safe_eval():
    assert safe_eval("(105.9 - 85.2) * 2") == pytest.approx(41.4)
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('x')")


def test_skills_load():
    names = set(Toolbox().skills)
    assert {"variance-analysis", "lead-lag", "power-signals"} <= names


# ------------------------------------------------------------------ agent loop
def scripted(steps):
    """Fake LLM: each step is either ('tool', name, input) or ('text', template). Templates can use
    {evidence[i]} from tool results seen so far."""
    state = {"i": 0}

    def llm(system, tools, messages):
        if len(messages) == 1:          # a new conversation starts the script over
            state["i"] = 0
        kind, *rest = steps[min(state["i"], len(steps) - 1)]
        state["i"] += 1
        if kind == "tool":
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": f"t{state['i']}", "name": rest[0], "input": rest[1]}]}
        results = [json.loads(b["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                   for b in m["content"]]
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": rest[0](results)}]}
    return llm


def test_agent_answer_with_tool_numbers_passes(dbp):
    llm = scripted([("tool", "quarterly_financials", {"last_n": 4}),
                    ("text", lambda r: f"In {r[0]['rows'][-1]['quarter']} NVIDIA revenue grew {r[0]['rows'][-1]['nvda_yoy_pct']}%.")])
    res = agent.run("growth?", llm, db_path=dbp)
    assert res.verification["ok"] and res.revisions == 0 and res.tool_calls[0]["name"] == "quarterly_financials"


def test_agent_made_up_number_forces_revision(dbp):
    llm = scripted([("tool", "quarterly_financials", {"last_n": 4}),
                    ("text", lambda r: "Revenue grew 999.9%."),                                   # fabricated
                    ("text", lambda r: f"Revenue grew {r[0]['rows'][-1]['nvda_yoy_pct']}%.")])   # revised
    res = agent.run("growth?", llm, db_path=dbp)
    assert res.revisions == 1 and res.verification["ok"]


def test_eval_cases_build_and_grade(dbp):
    cases = evals.build_cases(dbp)
    assert {c.id for c in cases} >= {"latest_nvda", "nowcast_honesty", "variance", "time_travel_grid"}
    tt = next(c for c in cases if c.id.startswith("time_travel_2"))
    assert tt.forbid and tt.as_of
    leaky = agent.Result(tt.question, tt.as_of, answer=f"Growth was {tt.forbid[0]}%.", verification={"ok": True})
    assert evals.grade(tt, leaky)["leaked_future"] == [tt.forbid[0]]


def test_receipts_track_which_tool_produced_each_number(dbp):
    llm = scripted([("tool", "quarterly_financials", {"last_n": 4}),
                    ("tool", "calculate", {"expression": "2 * 21.5"}),
                    ("text", lambda r: f"Growth was {r[0]['rows'][-1]['nvda_yoy_pct']}% and the doubled value is 43.0.")])
    res = agent.run("growth?", llm, db_path=dbp)
    srcs = {n["text"]: n["source"] for n in res.numbers}
    assert "calculate" in srcs.values() and "quarterly_financials" in srcs.values()
    assert all(n["verified"] for n in res.numbers)
    assert res.tool_calls[0]["numbers_returned"] > 0 and res.seconds >= 0


def test_numbers_quoted_in_question_may_be_repeated(dbp):
    llm = scripted([("tool", "quarterly_financials", {"last_n": 1}),
                    ("text", lambda r: f"No: not $250.0 billion; it was ${r[0]['rows'][-1]['nvda_revenue_usd_b']} billion.")])
    res = agent.run("My CFO says revenue was $250.0 billion. Right?", llm, db_path=dbp)
    assert res.verification["ok"]
    assert next(n for n in res.numbers if n["text"].startswith("$250"))["source"] == "question"


def test_new_tools_respect_point_in_time(dbp):
    tb_now, tb_past = Toolbox(dbp), Toolbox(dbp, as_of="2022-01-15")
    snap = tb_now.run("supply_chain_snapshot", {})
    assert any(c["ticker"] == "NVDA" for c in snap["companies"])
    past = tb_past.run("company_financials", {"ticker": "NVDA", "metric": "revenue", "last_n": 50})
    assert max(r["calendar_quarter"] for r in past["rows"]) <= "2021Q4"
    assert "error" in tb_now.run("company_financials", {"ticker": "ZZZ", "metric": "revenue"})
    zo = tb_past.run("zone_overview", {})
    assert zo["zones"][0]["zone"] == "DOM"
    assert "error" in tb_now.run("weather_adjusted", {"zone": "NOPE"})
    assert "steps" in tb_now.run("step_changes", {"zone": "DOM"})
    assert tb_now.run("restatements", {})["restatements"] == []


def test_refusal_and_claim_cases_grade(dbp):
    cases = {c.id: c for c in evals.build_cases(dbp)}
    nf = cases["no_forecast"]
    ok = agent.Result(nf.question, None, answer="I can't predict an exact figure.", verification={"ok": True})
    bad = agent.Result(nf.question, None, answer="It will be large.", verification={"ok": True})
    assert evals.grade(nf, ok)["passed"] and not evals.grade(nf, bad)["passed"]
    assert cases["claim_check"].category == "robustness" and cases["multi_step_ratio"].expect


def test_eval_main_end_to_end_with_fake_llm(dbp, tmp_path, monkeypatch):
    monkeypatch.setattr(evals, "OUT_DIR", tmp_path / "ev")
    llm = scripted([("tool", "quarterly_financials", {"last_n": 2}),
                    ("text", lambda r: f"Revenue grew {r[0]['rows'][-1]['nvda_yoy_pct']}%. I can't forecast.")])
    out = evals.main(["--only", "latest_nvda,no_forecast", "--models", "fake-a,fake-b"], llm=llm, db_path=dbp)
    assert out["total"] == 2 and len(out["leaderboard"]) == 2
    assert (tmp_path / "ev" / "results.md").exists() and out["by_category"]


def test_memo_writes_report_with_receipts(dbp, tmp_path, monkeypatch):
    from ftp import memo
    monkeypatch.setattr(memo, "OUT", tmp_path / "reports")
    llm = scripted([("tool", "quarterly_financials", {"last_n": 2}),
                    ("text", lambda r: f"### Spending\nCapex grew {r[0]['rows'][-1]['capex_yoy_pct']}%.")])
    out = memo.main(llm=llm, db_path=dbp)
    assert out["verification"]["ok"] and out["numbers"][0]["source"] == "quarterly_financials"
    assert (tmp_path / "reports" / "latest.json").exists() and len(list((tmp_path / "reports").glob("*.md"))) == 1


def test_citations_are_not_numeric_claims_and_quotes_pair_correctly():
    text = ('Paragraph "MD&A ¶35" holds it. Microsoft said "We will continue to invest in capital expenditures" '
            '[MSFT 10-Q filed 2026-10-28, MD&A ¶35] and Meta said "capital expenditures of approximately $135 billion" '
            '[META 10-Q filed 2026-10-22, MD&A ¶46].')
    assert [v for _, v, _, _ in verify.extract(text)] == [135.0]
    passages = ["We will continue to invest in capital expenditures to support growth.",
                "We anticipate capital expenditures of approximately $135 billion to $145 billion in 2026."]
    assert verify.check_quotes(text, passages) == []


def test_grader_accepts_more_precise_answers(dbp):
    case = evals.Case("x", "q", None, [0.52])
    res = agent.Result("q", None, answer="The correlation is 0.516.", verification={"ok": True})
    assert evals.grade(case, res)["missing"] == []
    res2 = agent.Result("q", None, answer="The correlation is 0.49.", verification={"ok": True})
    assert evals.grade(case, res2)["missing"] == [0.52]
