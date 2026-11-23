"""Filing text search (RAG): management's own words from 10-Q / 10-K filings, with citations.

XBRL gives clean totals but not the story (or segment details like NVIDIA's Data Center revenue).
This module:
  1. lists each company's recent 10-Q/10-K filings (SEC submissions API),
  2. downloads the primary document and extracts the MD&A section
     (10-Q Item 2 / 10-K Item 7, "Management's Discussion and Analysis"),
  3. splits it into ~1,200-character passages stored with filing date (point-in-time!),
  4. ranks passages for a question with BM25, a classic, transparent keyword-relevance score.

Every passage carries a citation like [NVDA 10-Q filed 2026-08-27, MD&A ¶14]. The agent must cite
passages it uses, and any text it puts in quotation marks is checked word-for-word against the
retrieved passages (verify.check_quotes).
"""
from __future__ import annotations

import html
import logging
import math
import re
import time
from collections import Counter
from html.parser import HTMLParser

import pandas as pd

from ftp import config, db
from ftp.eia import session

log = logging.getLogger(__name__)
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
FILING_TICKERS = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "ORCL", "AMD", "AVGO", "MU", "VRT", "EQIX", "D", "CEG", "VST"]
FILINGS_PER_COMPANY = 4
CHUNK_CHARS = 1200
TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOP = set("the a an and or of to in for on by with as at is are was were be been from that this these those it its "
           "our we us which also than other such may not no".split())


class _Text(HTMLParser):
    """HTML -> text, keeping block boundaries as newlines and skipping hidden inline-XBRL headers."""
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "ix:header") or "display:none" in (a.get("style") or "").replace(" ", ""):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "ix:header") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


MDA_START = re.compile(r"item\s*[27]\s*[.:\-–—]?\s*management[’'`s]*\s*discussion\s*and\s*analysis", re.I)
MDA_END = re.compile(r"\n\s*item\s*(3|7a)\s*[.:\-–—]?\s*quantitative", re.I)


def extract_mda(text: str) -> str:
    """The MD&A section. The heading also appears in the table of contents, so take the LONGEST span
    between a heading and the next 'Item 3/7A. Quantitative...' heading."""
    best = ""
    for m in MDA_START.finditer(text):
        end = MDA_END.search(text, m.end())
        span = text[m.start(): end.start() if end else m.start() + 150_000]
        if len(span) > len(best):
            best = span
    return best if len(best) > 2000 else ""


def chunk(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Paragraph-aware chunks of about `size` characters (a paragraph is never split unless it alone is too long)."""
    out, cur = [], ""
    for para in [p.strip() for p in text.split("\n") if p.strip()]:
        if len(para) < 40 and not re.search(r"\d", para) and cur:      # short headings stick to the next paragraph
            cur += "\n" + para
            continue
        if len(cur) + len(para) > size and cur:
            out.append(cur.strip())
            cur = ""
        while len(para) > size * 1.5:
            cut = para.rfind(". ", 0, size) + 1 or size
            out.append((cur + " " + para[:cut]).strip())
            cur, para = "", para[cut:].strip()
        cur += "\n" + para
    if cur.strip():
        out.append(cur.strip())
    return [c for c in out if len(c) > 80]


def list_filings(cik: int, s=None, n: int = FILINGS_PER_COMPANY) -> list[dict]:
    r = (s or session()).get(SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": config.SEC_USER_AGENT},
                             timeout=config.REQUEST_TIMEOUT_S)
    r.raise_for_status()
    rec = r.json()["filings"]["recent"]
    rows = [{"form": f, "accession": a, "filed_date": d, "report_date": rd, "doc": doc}
            for f, a, d, rd, doc in zip(rec["form"], rec["accessionNumber"], rec["filingDate"], rec["reportDate"],
                                        rec["primaryDocument"]) if f in ("10-Q", "10-K")]
    return rows[:n]


def load(conn, tickers=FILING_TICKERS, s=None) -> int:
    """Download and index new filings (already-indexed accessions are skipped: incremental)."""
    s = s or session()
    have = {r[0] for r in conn.execute("SELECT DISTINCT accession FROM filing_chunks")}
    total = 0
    for t in tickers:
        cik = config.COMPANIES[t]["cik"]
        try:
            for f in list_filings(cik, s):
                if f["accession"] in have:
                    continue
                time.sleep(0.2)
                url = ARCHIVE_URL.format(cik=cik, acc=f["accession"].replace("-", ""), doc=f["doc"])
                r = s.get(url, headers={"User-Agent": config.SEC_USER_AGENT}, timeout=config.REQUEST_TIMEOUT_S)
                r.raise_for_status()
                mda = extract_mda(html_to_text(r.text))
                parts = chunk(mda)
                df = pd.DataFrame({"ticker": t, "accession": f["accession"], "form": f["form"], "filed_date": f["filed_date"],
                                   "report_date": f["report_date"], "section": "MD&A", "chunk_id": range(len(parts)),
                                   "text": parts, "url": url})
                total += db.upsert(conn, "filing_chunks", df, ["accession", "chunk_id"])
                log.info("filing %s %s %s: %d passages", t, f["form"], f["filed_date"], len(parts))
            time.sleep(0.2)
        except Exception as exc:   # one company's odd filing shouldn't stop the rest
            log.error("filings %s failed: %s", t, exc)
    return total


def tokens(text: str) -> list[str]:
    return [w for w in TOKEN_RE.findall(text.lower()) if w not in STOP and len(w) > 1]


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75): rewards passages that contain rare query words often,
    normalized for passage length."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.toks = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.toks)) / max(len(self.toks), 1)
        df = Counter(w for t in self.toks for w in set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(t) for t in self.toks]

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        out = []
        for tf, t in zip(self.tf, self.toks):
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(t) / self.avg))
            out.append(s)
        return out


def search(conn, query: str, ticker: str | None = None, as_of: str | None = None, k: int = 5) -> list[dict]:
    sql = "SELECT ticker, accession, form, filed_date, report_date, chunk_id, text, url FROM filing_chunks WHERE filed_date <= ?"
    params: list = [as_of or "9999"]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return []
    sc = BM25(df["text"].tolist()).scores(query)
    df["score"] = sc
    # Small boost for recency: newer filings win ties.
    df["score"] += 0.15 * df["filed_date"].rank(pct=True)
    top = df[df["score"] > 0.2].sort_values("score", ascending=False).head(k)
    return [{"citation": f"[{r.ticker} {r.form} filed {r.filed_date}, MD&A ¶{r.chunk_id}]", "ticker": r.ticker,
             "form": r.form, "filed_date": r.filed_date, "period_end": r.report_date, "score": round(r.score, 2),
             "text": r.text, "url": r.url} for r in top.itertuples()]


# Retrieval benchmark: (ticker, question, phrase a relevant passage must contain). Scored daily, no LLM needed.
RETRIEVAL_BENCH = [
    ("NVDA", "what drove data center revenue", "data center"),
    ("NVDA", "gross margin change reasons", "gross margin"),
    ("MSFT", "Azure cloud services revenue growth", "azure"),
    ("GOOGL", "Google Cloud revenue growth", "google cloud"),
    ("AMZN", "AWS segment sales increase", "aws"),
    ("META", "capital expenditures servers data centers", "capital expenditures"),
    ("ORCL", "cloud infrastructure revenue", "infrastructure"),
    ("AMD", "data center segment revenue Instinct GPU", "data center"),
    ("AVGO", "AI semiconductor networking revenue", "semiconductor"),
    ("MU", "high-bandwidth memory HBM demand", "hbm"),
    ("VRT", "orders and backlog", "backlog"),
    ("EQIX", "interconnection revenue", "interconnection"),
    ("D", "data center customers electricity demand", "data center"),
    ("CEG", "nuclear generation output", "nuclear"),
    ("VST", "power prices and generation", "generation"),
]


def retrieval_eval(conn, k: int = 3) -> dict:
    """hit@k: is a passage containing the expected phrase in the top k? MRR: 1/rank of the first such passage."""
    rows = []
    for ticker, q, phrase in RETRIEVAL_BENCH:
        hits = search(conn, q, ticker, k=10)
        if not hits:
            continue
        rank = next((i + 1 for i, h in enumerate(hits) if phrase in h["text"].lower()), None)
        rows.append({"ticker": ticker, "query": q, "phrase": phrase, "rank": rank, "hit": bool(rank and rank <= k),
                     "top_citation": hits[0]["citation"]})
    if not rows:
        return {}
    return {"k": k, "queries": len(rows), "hit_rate": round(sum(r["hit"] for r in rows) / len(rows), 3),
            "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / len(rows), 3), "rows": rows}
