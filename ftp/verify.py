"""Number verifier: every figure in the agent's answer must trace back to evidence.

Evidence = every number returned by a tool during the run (including `calculate` results).
The verifier pulls numbers out of the answer text and checks each against the evidence,
allowing for normal rounding ("$96.2B" matches 96.221; "106%" matches 105.9).

Skipped (not claims about data): years (2019-2030), quarter labels (Q3, 2026Q2),
and small whole numbers 0-12 without units (counts like "3 quarters", list numbering).

This is deliberately strict and simple. It can't judge whether a sentence is TRUE, only
whether every number in it was actually produced by the data. That alone removes the most
dangerous failure in finance: a confident, made-up figure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM_RE = re.compile(
    r"(?<![\w.])(?P<neg>[-−])?\$?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?\s*"
    r"(?P<unit>%|pp|percentage points?|billion|bn|B\b|million|M\b|MW|GW|GWh|TWh|°C|x\b)?",
    re.IGNORECASE,
)
QUARTER_RE = re.compile(r"\b(?:Q[1-4]\s*\d{4}|\d{4}\s*Q[1-4]|Q[1-4])\b", re.IGNORECASE)
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
CITE_RE = re.compile(r"\[[A-Z]{1,5} 10-[QK][^\]]*\]|¶\s*\d+")   # [NVDA 10-Q filed …, MD&A ¶14] / ¶14: a source, not a claim


@dataclass
class Report:
    checked: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unverified

    def to_dict(self) -> dict:
        return {"numbers_checked": len(self.checked), "unverified": self.unverified, "ok": self.ok}


def extract(text: str) -> list[tuple[str, float, str, int]]:
    """(raw, value, unit, decimals) for each numeric claim, skipping years/quarters/dates/small counts."""
    clean = DATE_RE.sub(" ", QUARTER_RE.sub(" ", CITE_RE.sub(" ", text)))
    out = []
    for m in NUM_RE.finditer(clean):
        num = m.group("num").replace(",", "")
        dec = m.group("dec") or ""
        unit = (m.group("unit") or "").lower()
        value = float(num + dec) * (-1 if m.group("neg") else 1)
        decimals = len(dec) - 1 if dec else 0
        if not unit and not dec and 1900 <= value <= 2100:
            continue                                       # a year
        if not unit and not dec and 0 <= abs(value) <= 12:
            continue                                       # a count / list number
        out.append((m.group(0).strip(), value, unit, decimals))
    return out


def _candidates(value: float, unit: str) -> list[float]:
    """Values the number could equal in evidence units (tools report USD billions, MW, GWh, %)."""
    if unit in ("million", "m"):
        return [value / 1000, value]                       # $X million -> billions
    if unit == "gw":
        return [value * 1000, value]                       # GW -> MW
    if unit == "twh":
        return [value * 1000, value]                       # TWh -> GWh
    return [value, -value]                                 # "down 13%" may be stored as -13.2


def matches(value: float, unit: str, decimals: int, evidence: list[float]) -> bool:
    for v in _candidates(value, unit):
        tol = max(0.5 * 10 ** (-decimals), abs(v) * 0.005)  # half a unit in the last shown digit, or 0.5%
        if any(abs(v - e) <= tol + 1e-9 for e in evidence):
            return True
    return False


def verify(text: str, evidence: list[float]) -> Report:
    rep = Report()
    for raw, value, unit, decimals in extract(text):
        rep.checked.append(raw)
        if not matches(value, unit, decimals, evidence):
            rep.unverified.append(raw)
    return rep


def provenance(text: str, sources: list[tuple[float, str]], given: list[float] = ()) -> list[dict]:
    """For each number in the answer: was it verified, and which tool produced it?
    `sources` = (value, tool name) pairs; `given` = numbers quoted in the question itself."""
    out = []
    for raw, value, unit, decimals in extract(text):
        src = next((tool for v, tool in sources if matches(value, unit, decimals, [v])), None)
        if src is None and matches(value, unit, decimals, list(given)):
            src = "question"
        out.append({"text": raw, "verified": src is not None, "source": src})
    return out


QUOTE_RE = re.compile(r"\"([^\"]*)\"|“([^”]*)”")   # pair quotes left to right, THEN filter by length


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[’‘]", "'", t)).strip().lower()


def check_quotes(text: str, passages: list[str]) -> list[str]:
    """Every quotation (12+ chars in double quotes) must appear word-for-word in a retrieved passage."""
    corpus = _norm(" ".join(passages))
    quotes = [a or b for a, b in QUOTE_RE.findall(CITE_RE.sub(" ", text))]
    return [q for q in quotes if 12 <= len(q) <= 600 and _norm(q).strip(" .,") not in corpus]
