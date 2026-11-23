"""Filing text search: HTML parsing, MD&A extraction, chunking, BM25, point-in-time, quote checking."""
import pandas as pd

from fixtures import load_synthetic
from ftp import agent, db, filings, verify
from ftp.tools import Toolbox

HTML = """<html><body><ix:header><p>hidden xbrl header 999</p></ix:header>
<p>Table of Contents</p><p>Item 2. Management's Discussion and Analysis ..... 25</p><p>Item 3. Quantitative ..... 40</p>
<div><p>PART I</p></div>
<p>Item 2. Management&#8217;s Discussion and Analysis of Financial Condition and Results of Operations</p>
{body}
<p>Item 3. Quantitative and Qualitative Disclosures About Market Risk</p><p>Interest rate stuff.</p></body></html>"""
PARAS = ["<p>Data Center revenue for the second quarter was $41,096 million, up 56% from a year ago, driven by "
         "demand for our Blackwell computing platform from cloud service providers.</p>",
         "<p>Gaming revenue was $4.3 billion, up 49% from a year ago, reflecting higher sell-in to partners.</p>",
         "<p>Gross margin decreased to 72.4% due to inventory provisions and a mix shift toward systems.</p>"] * 6


def test_html_mda_and_chunks():
    text = filings.html_to_text(HTML.format(body="".join(PARAS)))
    assert "hidden xbrl" not in text and "Management’s" in text
    mda = filings.extract_mda(text)
    assert mda.startswith("Item 2. Management") and "Interest rate" not in mda and "Blackwell" in mda   # skips the TOC entry
    parts = filings.chunk(mda, size=400)
    assert len(parts) > 3 and all(len(p) < 900 for p in parts)


def _db_with_filings():
    conn = db.connect()
    load_synthetic(conn, True)
    rows = []
    for acc, filed, extra in [("a1", "2025-05-28", "Networking revenue grew on InfiniBand."),
                              ("a2", "2025-08-27", "Data Center revenue was $41,096 million, up 56%, driven by Blackwell.")]:
        for i, t in enumerate(["Gaming revenue was $4.3 billion, up 49% from a year ago.", extra,
                               "Operating expenses rose on compensation."]):
            rows.append({"ticker": "NVDA", "accession": acc, "form": "10-Q", "filed_date": filed, "report_date": filed,
                         "section": "MD&A", "chunk_id": i, "text": t, "url": "u"})
    db.upsert(conn, "filing_chunks", pd.DataFrame(rows), ["accession", "chunk_id"])
    return conn


def test_bm25_ranks_relevant_passage_first_and_respects_as_of():
    conn = _db_with_filings()
    top = filings.search(conn, "what drove data center revenue blackwell", "NVDA")
    assert "Blackwell" in top[0]["text"] and top[0]["citation"].startswith("[NVDA 10-Q filed 2025-08-27")
    past = filings.search(conn, "data center revenue blackwell", "NVDA", as_of="2025-06-01")
    assert all(p["filed_date"] <= "2025-06-01" for p in past) and not any("Blackwell" in p["text"] for p in past)


def test_quote_checker():
    passages = ["Data Center revenue was $41,096 million, up 56%, driven by Blackwell."]
    assert verify.check_quotes('Management said revenue was "driven by Blackwell" this quarter.', passages) == []
    assert verify.check_quotes('They said "driven by insatiable demand forever".', passages) == ["driven by insatiable demand forever"]


def test_filing_numbers_are_evidence_and_bad_quotes_force_revision(tmp_path):
    conn = _db_with_filings()
    conn.close()
    from ftp import config
    tb = Toolbox(config.DB_PATH)
    out = tb.run("search_filings", {"query": "data center revenue", "ticker": "NVDA"})
    assert out["passages"] and any(abs(e - 41.096) < 1e-6 for e in tb.evidence)
    steps = iter([
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "1", "name": "search_filings",
                                                 "input": {"query": "data center revenue", "ticker": "NVDA"}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": 'Data Center revenue was $41.1 billion, "driven by magic".'}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": 'Data Center revenue was $41.1 billion, "driven by Blackwell" '
                                                                         '[NVDA 10-Q filed 2025-08-27, MD&A ¶1].'}]}])
    res = agent.run("What drove data center revenue?", lambda *a: next(steps), db_path=config.DB_PATH)
    assert res.revisions == 1 and res.verification["ok"] and res.verification["bad_quotes"] == []


def test_retrieval_benchmark_scores():
    conn = _db_with_filings()
    r = filings.retrieval_eval(conn)
    nv = [x for x in r["rows"] if x["ticker"] == "NVDA"]
    assert r["queries"] == len(nv) >= 1 and nv[0]["hit"] and 0 < r["mrr"] <= 1
