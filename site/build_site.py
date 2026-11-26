"""Build Utkarsh Agarwal's personal site into site/public/ (home, /projects/, /about/).

The public site (utkarshagarwal.dev) holds only built pages; this script stays private.
Gigawatt Ledger figures are computed here from the project's open dataset (docs/data) and drawn in
the browser by a small canvas renderer. Sundown figures are drawn from /sundown/status.json, which
that pipeline republishes every morning. Snippets are real: code from both projects and answers the
AI analyst gave in its eval runs (site/snippets.json).

Usage:
  python site/build_site.py            # -> site/public/
  python site/build_site.py --preview  # also shows empty photo slots -> site/preview/
"""
from __future__ import annotations

import csv
import html
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "docs" / "data"
SNIPPETS = json.loads((HERE / "snippets.json").read_text())
HYPERSCALERS = ["MSFT", "GOOGL", "AMZN", "META"]
HS_NAMES = {"MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta"}
ZONES = [("DOM", "N. Virginia"), ("AEP", "Ohio, central U.S."), ("CE", "Chicago"), ("NCEN", "Dallas–Fort Worth"), ("FWES", "West Texas")]
PREVIEW = "--preview" in sys.argv
esc = html.escape

# =============================================================================================== content
# From the résumé and what Utkarsh shared. Phone number and private details stay off the site.
PROFILE = {"name": "Utkarsh Agarwal", "email": "autkarsh@berkeley.edu",
           "linkedin": "https://www.linkedin.com/in/utkarsh-agarwal-4078601b1/"}

EXPERIENCE = [
    ("2025", "Undergraduate data researcher", "University of Wisconsin–Madison",
     "Modeled dorm energy use in Python with faculty and PhD researchers and found about 12% in potential savings; "
     "mapped shuttle ridership to flag inefficient routes."),
    ("2023", "Software engineering intern", "Seaside Sustainability",
     "Built a full-stack Python and Node.js web app with an integrated donation system."),
]
LEADERSHIP = [
    ("2025–", "Project Lead", "Project Rishi, UC Berkeley",
     "Lead a team of 10 in a 40-member organization building an education center in rural India; own the grant applications and budget."),
    ("2019–23", "Varsity captain, cross country and track", "Saint Francis High School",
     "CIF California state cross country qualifier. 4:28 mile, 16:09 5K."),
]
EDUCATION = [
    ("2025–28", "B.A. Data Science and Applied Mathematics", "UC Berkeley",
     "Out-of-state transfer admit, about 40 a year. Linear algebra, discrete math, data structures, computer architecture."),
    ("2023–25", "Data Science and Economics", "University of Wisconsin–Madison",
     "Programming I and II, object-oriented programming, econometrics, microeconomic theory. Transferred."),
]
SKILLS = [
    ("Languages", "Python, SQL, R, Java, C, Swift"),
    ("Modeling", "Regression and fixed effects, time series, piecewise trends, backtesting, bootstrap"),
    ("AI systems", "LLM agents and tool use, RAG, eval harnesses, MCP, Claude and OpenAI APIs, PyTorch"),
    ("Data engineering", "pandas, SQLite, Spark, Airflow, Docker, GitHub Actions, pytest"),
]
PRS = [("Mile", "4:28"), ("3,200 m", "9:31"), ("5K", "16:09"), ("Half marathon", "1:19")]
PHOTOS = [("me.jpg", "", "tall"), ("running.jpg", "Cross country", "wide"), ("bmw.jpg", "The BMW", "sq"), ("berkeley.jpg", "Berkeley", "sq")]


# =============================================================================================== data
def _rows(name):
    p = DATA / name
    with p.open() as f:
        return list(csv.DictReader(f))


def hero_series():
    """Daily overnight load in Dominion (N. Virginia), weather-adjusted 30-day mean, and a one-break trend fit."""
    rows = [r for r in _rows("zone_daily.csv") if r["zone"] == "DOM"]
    dates = [r["date"] for r in rows]
    raw = np.array([float(r["overnight_mw"]) for r in rows])
    adj = np.array([float(r["normalized_mw"]) for r in rows])
    k = 15
    smooth = np.array([adj[max(0, i - k):i + k + 1].mean() for i in range(len(adj))])
    # continuous piecewise-linear fit on the monthly weather-adjusted series, breakpoint by least squares
    mon = [(r["month"], float(r["normalized_mw"])) for r in _rows("zone_weather_normalized.csv") if r["zone"] == "DOM"]
    y = np.array([v for _, v in mon]); t = np.arange(len(y)); best = None
    for b in range(18, len(y) - 12):
        X = np.c_[np.ones_like(t), t, np.maximum(0, t - b)]
        c, *_ = np.linalg.lstsq(X, y, rcond=None)
        sse = float(((X @ c - y) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, b, c)
    _, b, c = best
    s1, s2 = c[1] * 12, (c[1] + c[2]) * 12
    # fitted line at month midpoints -> map to daily index
    fit_m = [(mon[i][0], float(c[0] + c[1] * i + c[2] * max(0, i - b))) for i in range(len(mon))]
    base = np.mean([v for m, v in mon if m.startswith(mon[0][0][:4])])
    added = float(np.mean(y[-12:]) - base)
    return {
        "dates": dates, "raw": raw.round().astype(int).tolist(), "adj": smooth.round().astype(int).tolist(),
        "fit": [[m + "-15", round(v)] for m, v in fit_m], "brk": mon[b][0] + "-01",
        "s1": round(s1), "s2": round(s2), "brk_label": mon[b][0], "added": round(added),
    }


def capex_series():
    cap = defaultdict(dict)
    nv = {}
    for r in _rows("fin_quarterly.csv"):
        if r["metric"] == "capex" and r["ticker"] in HYPERSCALERS:
            cap[r["quarter"]][r["ticker"]] = float(r["value_usd"]) / 1e9
        if r["metric"] == "revenue" and r["ticker"] == "NVDA":
            nv[r["quarter"]] = float(r["value_usd"]) / 1e9
    qs = sorted(q for q, v in cap.items() if len(v) == 4 and q >= "2019Q1")
    return {"q": qs, "stack": {t: [round(cap[q][t], 2) for q in qs] for t in HYPERSCALERS},
            "names": [HS_NAMES[t] for t in HYPERSCALERS], "nvda": [round(nv[q], 2) if q in nv else None for q in qs]}


def zone_series():
    by = defaultdict(list)
    for r in _rows("zone_weather_normalized.csv"):
        if r["normalized_mw"]:
            by[r["zone"]].append((r["month"], float(r["normalized_mw"])))
    months = sorted({m for z in by.values() for m, _ in z})
    out = []
    for code, name in ZONES:
        s = dict(by[code])
        first = [v for m, v in sorted(s.items())[:12]]
        base = sum(first) / len(first)
        out.append({"name": name, "v": [round(100 * s[m] / base, 1) if m in s else None for m in months]})
    return {"months": months, "series": out}


def leadlag_series():
    """Correlation of hyperscaler capex YoY growth with N. Virginia overnight-load YoY growth at quarterly lags."""
    cap = capex_series()
    tot = {q: sum(cap["stack"][t][i] for t in HYPERSCALERS) for i, q in enumerate(cap["q"])}
    mon = defaultdict(list)
    for r in _rows("zone_monthly.csv"):
        if r["zone"] == "DOM" and r["overnight_mw"]:
            y, m = r["month"].split("-")
            mon[f"{y}Q{(int(m) - 1) // 3 + 1}"].append(float(r["overnight_mw"]))
    pw = {q: sum(v) / len(v) for q, v in mon.items() if len(v) == 3}
    def yoy(d):
        return {q: 100 * (v / d[f"{int(q[:4]) - 1}{q[4:]}"] - 1) for q, v in d.items() if f"{int(q[:4]) - 1}{q[4:]}" in d}
    cy, py = yoy(tot), yoy(pw)
    qs = sorted(set(cy) | set(py))
    idx = {q: i for i, q in enumerate(qs)}
    out = []
    for lag in range(-4, 5):
        pairs = [(cy[q], py[qs[idx[q] + lag]]) for q in cy if 0 <= idx[q] + lag < len(qs) and qs[idx[q] + lag] in py]
        if len(pairs) >= 8:
            a, b = np.array(pairs).T
            out.append({"lag": lag, "r": round(float(np.corrcoef(a, b)[0, 1]), 3), "n": len(pairs), "band": round(2 / len(pairs) ** 0.5, 3)})
    return out


def latest():
    return {r["metric"]: float(r["value"]) for r in _rows("history.csv")}


def code_excerpt(path: Path, start: str, stop: str | None = None, max_lines: int = 26) -> str:
    lines = path.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if start in l)
    j = next((n for n in range(i + 1, len(lines)) if stop and stop in lines[n]), len(lines))
    out = lines[i:min(j, i + max_lines)]
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _snippet(cache, src, start, stop, n=26):
    cached = HERE / cache
    if src.exists():
        cached.write_text(re.sub(r"\n{3,}", "\n\n", code_excerpt(src, start, stop, n)) + "\n")
    return cached.read_text().rstrip("\n") if cached.exists() else ""


def sql_snippet():
    return _snippet("snippet_netpeak.sql", ROOT.parent / "gp-repo" / "sql" / "analysis" / "06_net_peak_battery_share.sql", "daily_net_peak AS (", None)


def py_snippet():
    return _snippet("snippet_verify.py", ROOT / "ftp" / "verify.py", "def matches(", "def provenance(")


# =============================================================================================== text helpers
SQL_KW = r"\b(WITH|AS|SELECT|FROM|WHERE|AND|OR|JOIN|USING|ON|GROUP|BY|ORDER|DESC|ASC|OVER|PARTITION|CASE|WHEN|THEN|ELSE|END|SUM|COUNT|ROUND|NULLIF|COALESCE|ROW_NUMBER|IS|NOT|NULL)\b"
PY_KW = r"\b(def|return|for|in|if|not|any|max|abs|True|False|None|and|or)\b"


def highlight(code: str, lang: str) -> str:
    kw = SQL_KW if lang == "sql" else PY_KW
    com = r"--[^\n]*" if lang == "sql" else r"#[^\n]*"
    pat = re.compile(rf'(?P<c>{com})|(?P<s>\'[^\'\n]*\'|"[^"\n]*")|(?P<n>\b\d+(?:\.\d+)?(?:e-?\d+)?\b)|(?P<k>{kw})')
    out, pos = [], 0
    for m in pat.finditer(code):
        out.append(esc(code[pos:m.start()]))
        out.append(f'<span class="h{m.lastgroup}">{esc(m.group())}</span>')
        pos = m.end()
    out.append(esc(code[pos:]))
    return "".join(out)


def md_inline(text: str) -> str:
    out, in_ul, table = [], False, []
    inline = lambda s: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s))

    def flush():
        nonlocal table
        if table:
            rows_ = [r for r in table if not re.match(r"^\|[\s|:-]+\|$", r)]
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows_]
            out.append('<table class="ans"><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in cells[0]) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:]) + "</tbody></table>")
            table = []

    for line in text.strip().splitlines():
        s = line.strip()
        if s.startswith("|"):
            table.append(s); continue
        flush()
        if s.startswith("- "):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{inline(s[2:])}</li>"); continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        if s:
            out.append(f"<p>{inline(s)}</p>")
    flush()
    if in_ul:
        out.append("</ul>")
    return "".join(out)


def fig(fid: str, h: int, caption: str, n: int | None = None, cls: str = "", controls: str = "") -> str:
    num = f'<b>Figure {n}.</b> ' if n else ""
    return (f'<figure class="fig {cls}">{controls}<div class="plot" data-fig="{fid}" style="height:{h}px" role="img" aria-label="{esc(re.sub("<[^>]+>", "", caption))}"></div>'
            f'<figcaption>{num}{caption}</figcaption></figure>')


def window(wid, tabs):
    bar = "".join(f'<button role="tab" id="{wid}-t{i}" aria-controls="{wid}-p{i}" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}">{esc(l)}</button>'
                  for i, (l, _, _) in enumerate(tabs))
    panels = "".join(f'<div role="tabpanel" id="{wid}-p{i}" aria-labelledby="{wid}-t{i}" {"hidden" if i else ""} data-meta="{esc(m)}">{b}</div>'
                     for i, (_, m, b) in enumerate(tabs))
    return (f'<div class="win" data-win><div class="bar"><div role="tablist" aria-label="Output and code">{bar}</div>'
            f'<span class="meta">{esc(tabs[0][1])}</span></div><div class="body">{panels}</div></div>')


def ask_panel():
    a, b = SNIPPETS["time_travel_2025Q1"], SNIPPETS["time_travel_2025Q2"]
    ans = lambda c: md_inline(c["answer"].split("\n\nThe data show")[0])
    return (f'<div class="ask"><p class="q">&gt; {esc(a["question"])}</p>'
            f'<div class="asof" role="radiogroup" aria-label="Date the question is asked"><span>as of</span>'
            f'<button role="radio" aria-checked="true" data-asof="0">{a["as_of"]}</button>'
            f'<button role="radio" aria-checked="false" data-asof="1">{b["as_of"]}</button></div>'
            f'<div class="a" data-ans="0">{ans(a)}</div><div class="a" data-ans="1" hidden>{ans(b)}</div>'
            f'<p class="note">Two real answers from eval runs. The analyst only sees filings made public by the date it is asked, so the same question gets a different, correct answer.</p></div>')


def ratio_panel():
    c = SNIPPETS["multi_step_ratio"]
    return (f'<div class="ask"><p class="q">&gt; {esc(c["question"])}</p><div class="a">{md_inline(c["answer"].split(chr(10) + chr(10) + "The ratio dipped")[0])}</div>'
            f'<p class="note">Every number in the answer was checked against the data before it was shown.</p></div>')


def spec(rows):
    return '<dl class="spec">' + "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in rows) + "</dl>"


def entries(items):
    return '<ul class="entries">' + "".join(
        f'<li><span class="yr">{esc(w)}</span><div><h3>{esc(t)} <span class="org">{esc(o)}</span></h3><p>{esc(d)}</p></div></li>'
        for w, t, o, d in items) + "</ul>"


# =============================================================================================== pages
def reel(H):
    """The home page figure: five real figures that rotate, each drawing itself in, caption swapping with it."""
    cap = capex_series()
    q = cap["q"][-1]
    cap_now = sum(cap["stack"][t][-1] for t in HYPERSCALERS)
    z = zone_series()
    top = max(z["series"], key=lambda s_: next(v for v in reversed(s_["v"]) if v is not None))
    top_v = next(v for v in reversed(top["v"]) if v is not None)
    slides = [
        ("hero", "Northern Virginia load",
         f"Overnight electricity load in Dominion, the Northern Virginia grid zone that serves the largest data-center market in the world. "
         f"Dots are daily readings; the line is a weather-adjusted 30-day mean; the dashed line is a one-break linear trend fit by least squares. "
         f"Growth went from <b>+{H['s1']:,} MW/yr</b> to <b>+{H['s2']:,} MW/yr</b> in {month_name(H['brk_label'])}."),
        ("capex", "Big tech capex",
         f"Quarterly capital spending by Microsoft, Alphabet, Amazon and Meta (stacked) against NVIDIA&rsquo;s quarterly revenue (line). "
         f"{q[:4]} Q{q[-1]}: <b>${cap_now:.1f}B</b> of capex in one quarter."),
        ("zones", "Five regions",
         f"Weather-adjusted overnight load in five data-center regions, indexed to each region&rsquo;s first 12 months = 100. "
         f"{top['name']} is up <b>{top_v - 100:.0f}%</b>."),
        ("heat", "Battery heatmap",
         "California battery output by hour of day (rows) and week (columns). Blue is charging on midday solar; orange is discharging after sunset. "
         "Refreshed every morning by Sundown."),
        ("duck", "A day on the grid",
         "California on an average day: demand (line), solar (shaded) and batteries (bars; below zero is charging), in GW. "
         "Batteries soak up the solar peak and return it in the evening."),
    ]
    tabs = "".join(f'<button role="tab" id="rt{i}" aria-controls="rp{i}" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}" data-cap="{esc(c, quote=True)}">'
                   f'<span>{i + 1}</span>{esc(n)}</button>' for i, (_, n, c) in enumerate(slides))
    panels = "".join(f'<div class="slide{" on" if i == 0 else ""}" role="tabpanel" id="rp{i}" aria-labelledby="rt{i}"><div class="plot" data-fig="{f}" style="height:100%"></div></div>'
                     for i, (f, _, _) in enumerate(slides))
    return (f'<figure class="reel" data-reel aria-roledescription="carousel" aria-label="Figures from my pipelines">'
            f'<div class="reel-bar"><div role="tablist" aria-label="Choose a figure">{tabs}</div>'
            f'<button class="pause" type="button" aria-pressed="false">Pause</button></div>'
            f'<div class="prog" aria-hidden="true"><i></i></div>'
            f'<div class="stage">{panels}</div>'
            f'<figcaption aria-live="polite"><b>Figure <span class="n">1</span>.</b> <span class="t">{slides[0][2]}</span> Hover for values.</figcaption></figure>')


def home(H, L):
    results = [
        ("22 / 22", "eval cases passed by the Gigawatt Ledger AI analyst, three runs each, prompt injection included", "/projects/#gigawatt-ledger"),
        (f"+{H['added']:,} MW", "weather-adjusted always-on load added in Northern Virginia since 2019, from 337,000+ grid hours", "/projects/#gigawatt-ledger"),
        ("3", "errors found in the U.S. Energy Information Administration's published grid data by Sundown's quality checks", "/projects/#sundown"),
        ('<span data-live="sd-rows">588,712</span>', "hourly records across 7 U.S. grids, validated and reloaded every morning", "/projects/#sundown"),
        ("~40 / yr", "out-of-state transfer admits to UC Berkeley; studying data science and applied math", "/about/"),
        ("16:09", "5K as a high school runner; CIF California state cross country qualifier", "/about/"),
    ]
    res = "".join(f'<tr><th scope="row">{v}</th><td><a href="{href}">{esc(t)}</a></td></tr>' for v, t, href in results)
    body = f"""
  <div class="hero">
    <h1>{PROFILE['name']}</h1>
    <p class="lede">Data science and applied math at UC Berkeley. I build data pipelines and statistical models on public energy and financial data, and the AI tools that query them.</p>
    <p class="links"><a href="mailto:{PROFILE['email']}">{PROFILE['email']}</a><a href="{PROFILE['linkedin']}">LinkedIn</a><a href="/projects/">Projects</a></p>
  </div>

  {reel(H)}
  <p class="status"><i class="dot" data-live="gl-dot"></i><span data-live="gl-when">Updated daily</span> by the Gigawatt Ledger and Sundown pipelines. <span data-live="gl-what"></span></p>

  <section class="results"><h2>Selected results</h2><table>{res}</table></section>

  <section class="work"><h2>Projects</h2><div>
    <article class="card">
      <div><h3><a href="/projects/#gigawatt-ledger">Gigawatt Ledger</a></h3>
        <p>SEC filings from 26 AI-supply-chain companies joined to hourly grid demand in five data-center regions, stored point-in-time. An AI analyst answers questions over it, and code verifies every number it writes.</p>
        <p class="tags">Python, SQL, Claude API, RAG, evals</p></div>
      {fig("capex_mini", 150, "Big tech capex per quarter, $B.", None, "mini")}
    </article>
    <article class="card">
      <div><h3><a href="/projects/#sundown">Sundown</a></h3>
        <p>A daily pipeline for the seven major U.S. grids that measures how much of the evening peak batteries now carry, with automated quality checks on the government's source data.</p>
        <p class="tags">Python, SQL, Airflow, Spark, pytest</p></div>
      {fig("heat_mini", 150, "California battery output by hour (rows) and week (columns): blue charging, orange discharging.", None, "mini")}
    </article>
    <p class="more"><a href="/projects/">All projects, figures and code</a></p>
  </div></section>

  <section><h2>Experience</h2>{entries(EXPERIENCE)}</section>
  <section><h2>Leadership and athletics</h2>{entries(LEADERSHIP)}</section>
  <section><h2>Education</h2>{entries(EDUCATION)}</section>
  <section><h2>Skills</h2>{spec([(k, esc(v)) for k, v in SKILLS])}</section>"""
    return layout(PROFILE["name"], "Data science and applied math at UC Berkeley. Data pipelines, statistical models and AI tools on public energy and financial data.",
                  body, "home", {"hero": H, "capex": capex_series(), "zones": zone_series()})


def month_name(ym: str) -> str:
    import calendar
    y, m = ym.split("-")
    return f"{calendar.month_name[int(m)]} {y}"


def projects_page(H, L):
    cap = capex_series()
    q = cap["q"][-1]
    cap_now = sum(cap["stack"][t][-1] for t in HYPERSCALERS)
    ll = leadlag_series()
    best = max(ll, key=lambda d: d["r"])
    gl = window("gl", [
        ("Time travel", "AI analyst, point-in-time", ask_panel()),
        ("Multi-step", "AI analyst, calculation", ratio_panel()),
        ("Code", "ftp/verify.py", f'<pre class="code"><code>{highlight(py_snippet(), "py")}</code></pre>'),
    ])
    sd = window("sd", [("SQL", "sql/analysis/06_net_peak_battery_share.sql", f'<pre class="code"><code>{highlight(sql_snippet(), "sql")}</code></pre>')])
    heat_ctl = ('<div class="seg" role="radiogroup" aria-label="Grid"><button role="radio" aria-checked="true" data-region="CISO">California</button>'
                '<button role="radio" aria-checked="false" data-region="ERCO">Texas</button></div>')
    body = f"""
  <div class="phead">
    <h1>Projects</h1>
    <p class="lede">Two data systems that run every morning without me, and one car. Figures on this page are drawn from their current data.</p>
    <p class="links"><a href="#gigawatt-ledger">Gigawatt Ledger</a><a href="#sundown">Sundown</a><a href="#bmw">BMW engine tuning</a></p>
  </div>

  <article class="proj" id="gigawatt-ledger">
    <header><h2>Gigawatt Ledger</h2><p class="sub">AI spending traced from big tech&rsquo;s filings to the power grid</p></header>
    <div class="cols">
      <div class="text">
        <p>Each night the pipeline pulls SEC filings for 26 companies across the AI supply chain (cloud buyers, chip makers, server
        and power-equipment suppliers, data-center landlords, utilities) and hourly electricity demand for five data-center regions.
        Every value is stored with the date it became public, so any past day can be replayed exactly as it looked then.</p>
        <p>Demand is weather-adjusted with a regression on month fixed effects and quadratic heating and cooling degree-days,
        then split into growth regimes with a piecewise-linear trend whose breakpoints are chosen by BIC.</p>
        <p>On top sits an AI analyst with 16 tools. A verifier checks every number in its answer against the tool outputs before
        anything is shown, and an eval suite of 22 cases (time travel, multi-step math, unit traps, prompt injection) runs it end to end.</p>
        <p class="links"><a href="/gigawatt-ledger/">Open the live dashboard</a><a href="/gigawatt-ledger/data/README.md">Open dataset</a></p>
      </div>
      {spec([("Companies", "26, from SEC EDGAR XBRL"), ("Grid data", "337,000+ hours, 5 regions"), ("Filing passages", "4,600+, BM25 search"),
             ("Evals", "22 / 22, three runs each"), ("Tests", "63, run on every push"), ("Runs", "Daily, GitHub Actions")])}
    </div>
    <div class="m"><span>Weather model</span><code>load<sub>t</sub> = &alpha;<sub>month</sub> + &beta;<sub>1</sub>HDD<sub>t</sub> + &beta;<sub>2</sub>HDD<sub>t</sub><sup>2</sup> + &beta;<sub>3</sub>CDD<sub>t</sub> + &beta;<sub>4</sub>CDD<sub>t</sub><sup>2</sup> + &gamma;&middot;weekend<sub>t</sub> + &epsilon;<sub>t</sub></code></div>
    {fig("capex", 330, f"Quarterly capital spending by the four largest cloud companies (stacked), against NVIDIA&rsquo;s quarterly revenue (line). {q[:4]} Q{q[-1]}: <b>${cap_now:.1f}B</b> of capex.", 2)}
    <div class="pair">
      {fig("zones", 300, "Weather-adjusted overnight load by data-center region, indexed to each region&rsquo;s first 12 months = 100.", 3)}
      {fig("leadlag", 300, f"Correlation between capex growth and N. Virginia load growth when capex leads by k quarters. Shaded: &plusmn;2/&radic;n noise band. Strongest at k = {best['lag']:+d} (r = {best['r']:.2f}, n = {best['n']}).", 4)}
    </div>
    {gl}
  </article>

  <article class="proj" id="sundown">
    <header><h2>Sundown</h2><p class="sub">How much of the evening peak batteries now carry on U.S. grids</p></header>
    <div class="cols">
      <div class="text">
        <p>Every morning the pipeline pulls the previous day of hourly demand and generation by fuel for the seven major U.S. grids
        from the EIA API, validates it, loads it into SQL, and rebuilds a dashboard. A Spark job recomputes the main metric
        independently and must match the SQL result.</p>
        <p>The headline metric is the battery share of the <i>net</i> peak: the hour each day when demand minus solar and wind is highest,
        which is the hour the rest of the fleet struggles to cover. The quality checks have caught three errors in EIA&rsquo;s own published data.</p>
        <p class="links"><a href="/sundown/">Open the live dashboard</a></p>
      </div>
      {spec([("Grids", "7 balancing authorities"), ("Records", '<span data-live="sd-rows">588,712</span> hourly rows'),
             ("California net peak from batteries", '<span data-live="sd-share">25%</span>, last 30 days'), ("Tests", "59"), ("Runs", "Daily, Airflow and GitHub Actions")])}
    </div>
    {fig("heat", 330, "Battery output by hour of day (rows) and week (columns), MW. Blue is charging, orange is discharging. The midday blue band is solar being stored; the orange band is that energy coming back after sunset.", 5, "", heat_ctl)}
    <div class="pair">
      {fig("duck", 280, "California, an average day: demand (line), solar (shaded) and batteries (bars, below zero = charging), GW.", 6)}
      {fig("net", 280, "Share of California&rsquo;s daily net peak supplied by batteries, 7-day mean, %.", 7)}
    </div>
    {fig("monthly", 280, "Energy discharged by batteries each month, by grid, GWh.", 8)}
    {sd}
  </article>

  <article class="proj" id="bmw">
    <header><h2>BMW engine and electronics tuning</h2><p class="sub">Personal project, 2026</p></header>
    <div class="cols">
      <div class="text">
        <p>Reprogrammed the car&rsquo;s engine control unit in C and Python, tuning 125 calibration parameters across ignition timing,
        fuel delivery, boost and throttle response. Reconfigured 6 electronic control modules to add live G-force telemetry and
        radar displays to the car&rsquo;s own screens.</p>
      </div>
      {spec([("Parameters tuned", "125"), ("Engine performance", "+17%"), ("Modules reconfigured", "6"), ("Built with", "C, Python")])}
    </div>
    {'<figure class="photo"><img src="/photos/bmw.jpg" alt="The BMW" loading="lazy"></figure>' if (HERE / "photos" / "bmw.jpg").exists() else ""}
  </article>"""
    return layout(f"Projects | {PROFILE['name']}", "Gigawatt Ledger and Sundown: data pipelines, statistical models and an AI analyst on public energy and financial data.",
                  body, "projects", {"capex": cap, "zones": zone_series(), "leadlag": ll})


def about_page():
    have = [(f, c, s) for f, c, s in PHOTOS if (HERE / "photos" / f).exists()]
    tiles = [f'<figure class="ph {s}"><img src="/photos/{f}" alt="{esc(c or PROFILE["name"])}" loading="lazy"></figure>' for f, c, s in have]
    if PREVIEW:
        tiles += [f'<div class="ph {s} slot"><small>site/photos/{f}</small></div>' for f, c, s in PHOTOS if (f, c, s) not in have]
    gallery = f'<div class="gallery">{"".join(tiles)}</div>' if tiles else ""
    prs = "".join(f"<tr><th scope='row'>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in PRS)
    body = f"""
  <div class="phead">
    <h1>About</h1>
  </div>
  <div class="about">
    <div class="prose">
      <p>I grew up in Mountain View. In high school I was a distance runner: varsity captain at Saint Francis, a CIF California state
      cross country qualifier, and the age-group winner at the 2022 San Francisco Half Marathon when I was 17.</p>
      <p>I started college at Wisconsin in data science and economics, then transferred to Berkeley, which takes about 40 out-of-state
      transfers a year. I study data science and applied math, and I am a project lead at Project Rishi, a 40-person group
      building an education center in rural India.</p>
      <p>The projects on this site run every morning on their own. When I&rsquo;m not working on them I&rsquo;m usually reprogramming a car.</p>
      <p class="links"><a href="mailto:{PROFILE['email']}">{PROFILE['email']}</a><a href="{PROFILE['linkedin']}">LinkedIn</a></p>
    </div>
    <table class="prs"><caption>Personal records</caption>{prs}</table>
  </div>
  {gallery}"""
    return layout(f"About | {PROFILE['name']}", "Utkarsh Agarwal: data science and applied math at UC Berkeley, former state-level distance runner.", body, "about", {})


# =============================================================================================== layout
CSS = r"""
:root{--bg:#ffffff;--ink:#141619;--sec:#565c64;--mute:#868c94;--rule:#e4e6e9;--soft:#f5f6f7;--grid:#eef0f2;
  --c1:#d9480f;--c2:#2a78d6;--c3:#1baf7a;--c4:#c98500;--c5:#9a4fd0;--neg:#2a78d6;--pos:#d9480f;--ok:#1d8a4e;
  --kw:#2a6fd0;--str:#1f7a4a;--num:#b4561b;--com:#8a9098;
  --sans:"IBM Plex Sans",system-ui,sans-serif;--serif:"Source Serif 4",Georgia,serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
:root[data-theme=dark]{--bg:#101214;--ink:#e9ebee;--sec:#a5abb3;--mute:#737982;--rule:#24282e;--soft:#16191c;--grid:#1c2024;
  --c1:#d95926;--c2:#3987e5;--c3:#199e70;--c4:#c98500;--c5:#9085e9;--neg:#3987e5;--pos:#d95926;--ok:#3fb879;--kw:#7fa6f0;--str:#7fd1a0;--num:#f0a46b;--com:#6d737d}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#101214;--ink:#e9ebee;--sec:#a5abb3;--mute:#737982;--rule:#24282e;--soft:#16191c;--grid:#1c2024;
  --c1:#d95926;--c2:#3987e5;--c3:#199e70;--c4:#c98500;--c5:#9085e9;--neg:#3987e5;--pos:#d95926;--ok:#3fb879;--kw:#7fa6f0;--str:#7fd1a0;--num:#f0a46b;--com:#6d737d}}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:72px}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.6 var(--sans);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
::selection{background:color-mix(in srgb,var(--c1) 25%,transparent)}
a{color:inherit;text-decoration:underline;text-decoration-color:color-mix(in srgb,var(--ink) 25%,transparent);text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{text-decoration-color:var(--c1);text-decoration-thickness:2px}
a:focus-visible,button:focus-visible{outline:2px solid var(--c1);outline-offset:2px}
main{max-width:1080px;margin:0 auto;padding:0 40px}
header.top{position:sticky;top:0;z-index:9;background:color-mix(in srgb,var(--bg) 90%,transparent);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--rule)}
.top .in{max-width:1080px;margin:0 auto;padding:0 40px;height:56px;display:flex;align-items:center;justify-content:space-between}
.top .me{display:flex;gap:10px;align-items:center;text-decoration:none;font-weight:640;letter-spacing:-.01em}
.top .me svg{width:28px;height:28px;display:block;color:var(--ink)}
.top nav{display:flex;gap:24px;align-items:center;font-size:15px}
.top nav>a,.top nav .dd>a{text-decoration:none;color:var(--sec)}.top nav>a:hover,.top nav .dd>a:hover,.top nav a[aria-current=page]{color:var(--ink)}
.top nav a[aria-current=page]{box-shadow:inset 0 -2px 0 var(--ink);padding:17px 0}
.dd{position:relative}
.dd .menu{position:absolute;right:-16px;top:100%;margin-top:6px;width:320px;background:var(--bg);border:1px solid var(--rule);border-radius:6px;padding:6px;box-shadow:0 10px 30px -12px rgba(0,0,0,.25);opacity:0;visibility:hidden;transform:translateY(-4px);transition:opacity .15s,transform .15s,visibility 0s .15s}
.dd::after{content:"";position:absolute;left:-10px;right:-10px;top:100%;height:12px}
.dd:hover .menu,.dd:focus-within .menu{opacity:1;visibility:visible;transform:none;transition:opacity .15s,transform .15s}
.dd .menu>a{display:block;padding:9px 10px;border-radius:4px;text-decoration:none;color:var(--ink)}
.dd .menu>a:hover,.dd .menu>a:focus-visible{background:var(--soft)}
.dd .menu b{display:block;font-weight:560;font-size:14.5px}
.dd .menu span{display:block;font-size:13px;color:var(--sec);margin-top:1px}
.dd .menu p{margin:4px 0 0;padding:9px 10px 4px;border-top:1px solid var(--rule);display:flex;gap:16px;font-size:13px}
.dd .menu p a{color:var(--sec)}.dd .menu p a:hover{color:var(--ink)}
.top nav .dd>a{display:inline-block}
#theme{font:inherit;font-size:14px;color:var(--sec);background:none;border:1px solid var(--rule);border-radius:5px;padding:2px 10px;cursor:pointer}
#theme:hover{color:var(--ink);border-color:var(--mute)}

h1{font:500 64px/1 var(--serif);letter-spacing:-.022em;margin:0}
.hero{padding:72px 0 8px;max-width:760px}
.phead{padding:72px 0 0;max-width:760px}
.lede{font:400 21px/1.5 var(--serif);color:var(--sec);margin:18px 0 0}
.links{display:flex;flex-wrap:wrap;gap:6px 22px;margin:18px 0 0;font-size:15px}

.reel{margin:44px 0 0}
.reel-bar{display:flex;justify-content:space-between;align-items:center;gap:16px;border-bottom:1px solid var(--rule)}
.reel [role=tablist]{display:flex;overflow-x:auto;scrollbar-width:none}
.reel [role=tab]{font:500 14px var(--sans);color:var(--sec);background:none;border:0;padding:10px 16px 10px 0;margin-right:6px;cursor:pointer;white-space:nowrap}
.reel [role=tab] span{display:inline-block;min-width:1.4em;color:var(--mute);font-variant-numeric:tabular-nums}
.reel [role=tab]:hover,.reel [role=tab][aria-selected=true]{color:var(--ink)}
.reel [role=tab][aria-selected=true] span{color:var(--c1)}
.reel .pause{font:500 13px var(--sans);color:var(--sec);background:none;border:1px solid var(--rule);border-radius:5px;padding:2px 10px;cursor:pointer;flex:none}
.reel .pause:hover{color:var(--ink);border-color:var(--mute)}
.prog{height:2px;background:transparent;margin-top:-1px}
.prog i{display:block;height:2px;width:0;background:var(--c1)}
.stage{position:relative;height:380px;margin-top:22px}
.slide{position:absolute;inset:0;opacity:0;visibility:hidden;transition:opacity .45s ease,visibility 0s linear .45s}
.slide.on{opacity:1;visibility:visible;transition:opacity .45s ease}
.reel figcaption{font-size:13.5px;line-height:1.5;color:var(--sec);margin-top:12px;max-width:760px;min-height:4.5em}
.reel figcaption b{color:var(--ink);font-weight:600}
@media (prefers-reduced-motion:reduce){.slide,.slide.on{transition:none}}
.fig{margin:40px 0 0}
.fig.wide{margin-top:44px}
.plot{position:relative;width:100%;cursor:crosshair;touch-action:pan-y}
.plot canvas{display:block;width:100%;height:100%}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);font:500 12.5px/1.45 var(--sans);padding:6px 9px;border-radius:4px;white-space:nowrap;transform:translate(-50%,-100%);margin-top:-10px;z-index:2}
.tip b{font-weight:650}
.fig figcaption{font-size:13.5px;line-height:1.5;color:var(--sec);margin-top:12px;max-width:760px}
.fig figcaption b{color:var(--ink);font-weight:620}
.status{font-size:13.5px;color:var(--mute);margin:10px 0 0}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--mute);margin-right:8px;vertical-align:1px}
.dot.ok{background:var(--ok)}

section{margin-top:88px;display:grid;grid-template-columns:180px minmax(0,1fr);gap:0 40px}
section.results{margin-top:72px}
section>h2{font-size:15px;font-weight:620;margin:2px 0 0;color:var(--ink)}
.results table{width:100%;border-collapse:collapse}
.results th{font:500 26px/1.2 var(--serif);letter-spacing:-.012em;text-align:left;white-space:nowrap;padding:12px 28px 12px 0;vertical-align:baseline;border-top:1px solid var(--rule)}
.results td{padding:12px 0;border-top:1px solid var(--rule);color:var(--sec);vertical-align:baseline}
.results td a{text-decoration:none}.results td a:hover{color:var(--ink);text-decoration:underline;text-decoration-color:var(--c1)}
.results tr:last-child>*{border-bottom:1px solid var(--rule)}

.work .card{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:32px;padding:22px 0;border-top:1px solid var(--rule);align-items:start}
.work .card:nth-of-type(2){border-bottom:1px solid var(--rule)}
.work h3{font:500 23px/1.2 var(--serif);letter-spacing:-.01em;margin:0 0 6px}
.work h3 a{text-decoration:none}.work h3 a:hover{text-decoration:underline;text-decoration-color:var(--c1)}
.work .card p{margin:0 0 8px;color:var(--sec);max-width:34em;font:16.5px/1.6 var(--serif)}
.work .card p.tags{font:13.5px/1.5 var(--sans)}
.tags{font-size:13.5px;color:var(--mute)}
.fig.mini{margin:4px 0 0}.fig.mini figcaption{font-size:12.5px;margin-top:6px;color:var(--mute)}
.more{margin:18px 0 0;font-weight:560}

.entries{list-style:none;margin:0;padding:0}
.entries li{display:grid;grid-template-columns:72px minmax(0,1fr);gap:16px;padding:14px 0;border-top:1px solid var(--rule)}
.entries li:last-child{border-bottom:1px solid var(--rule)}
.entries .yr{color:var(--mute);font-size:14px;padding-top:2px}
.entries h3{font-size:16.5px;font-weight:620;margin:0;letter-spacing:-.008em}
.entries .org{font-weight:400;color:var(--sec)}
.entries p{margin:3px 0 0;color:var(--sec);max-width:40em;font:16px/1.6 var(--serif)}
.spec{margin:0;border-top:1px solid var(--rule)}
.spec div{display:grid;grid-template-columns:150px minmax(0,1fr);gap:16px;padding:9px 0;border-bottom:1px solid var(--rule);font-size:15px}
.spec dt{color:var(--sec)}.spec dd{margin:0}

.proj{margin-top:104px;scroll-margin-top:72px}
.proj+.proj{margin-top:136px}
.proj header h2{font:500 44px/1.05 var(--serif);letter-spacing:-.018em;margin:0}
.proj .sub{font:italic 400 19px/1.4 var(--serif);color:var(--sec);margin:8px 0 0}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:48px;margin-top:28px;align-items:start}
.cols .text p{margin:0 0 14px;max-width:38em;font:17px/1.65 var(--serif)}
.cols .text p.links{font:15px var(--sans)}
.cols .spec{font-size:14.5px}.cols .spec div{grid-template-columns:minmax(0,1fr) auto;text-align:right}.cols .spec dt{text-align:left}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:40px}
.m{margin:36px 0 0;padding:14px 0;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);display:flex;gap:24px;align-items:baseline;overflow-x:auto}
.m span{font-size:13.5px;color:var(--sec);white-space:nowrap}
.m code{font:italic 18px/1.4 var(--serif);white-space:nowrap;color:var(--ink)}
.m sub,.m sup{font-size:.7em;font-style:italic}
.seg{display:inline-flex;border:1px solid var(--rule);border-radius:5px;margin-bottom:10px;overflow:hidden}
.seg button{font:500 13px var(--sans);color:var(--sec);background:none;border:0;padding:4px 12px;cursor:pointer}
.seg button+button{border-left:1px solid var(--rule)}
.seg button[aria-checked=true]{background:var(--ink);color:var(--bg)}

.win{margin:48px 0 0;border:1px solid var(--rule);border-radius:7px;overflow:hidden}
.win .bar{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:0 14px 0 4px;border-bottom:1px solid var(--rule);background:var(--soft)}
.win [role=tablist]{display:flex;overflow-x:auto;scrollbar-width:none}
.win [role=tab]{font:500 14px var(--sans);color:var(--sec);background:none;border:0;padding:10px 12px;cursor:pointer;white-space:nowrap}
.win [role=tab]:hover,.win [role=tab][aria-selected=true]{color:var(--ink)}
.win [role=tab][aria-selected=true]{box-shadow:inset 0 -2px 0 var(--c1)}
.win .bar .meta{font:12.5px var(--mono);color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.win .body{padding:22px 24px}
.code{margin:0;font:13px/1.7 var(--mono);overflow-x:auto;white-space:pre;tab-size:4}
.hc{color:var(--com);font-style:italic}.hs{color:var(--str)}.hn{color:var(--num)}.hk{color:var(--kw)}
.ask .q{margin:0 0 14px;font:500 14px var(--mono)}
.asof{display:flex;align-items:center;gap:6px;margin:0 0 16px;font-size:13px;color:var(--mute)}
.asof span{margin-right:4px}
.asof button{font:500 12.5px var(--mono);color:var(--sec);background:none;border:1px solid var(--rule);border-radius:4px;padding:3px 8px;cursor:pointer}
.asof button[aria-checked=true]{color:var(--bg);background:var(--ink);border-color:var(--ink)}
.ask .a{border-left:2px solid var(--c1);padding-left:16px;font:16px/1.6 var(--serif)}
.ask .a p{margin:0 0 9px}.ask .a ul{margin:0 0 9px;padding-left:18px}
.ask .note{font-size:13px;color:var(--mute);margin:14px 0 0;max-width:46em}
table.ans{border-collapse:collapse;margin:4px 0 10px;font-size:14px}
table.ans th,table.ans td{padding:3px 22px 3px 0;border-bottom:1px solid var(--rule);text-align:left}
table.ans th{font-weight:560;color:var(--sec)}
.photo{margin:32px 0 0}.photo img{width:100%;display:block;border-radius:3px}

.about{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:64px;margin-top:32px;align-items:start}
.prose p{font:400 19.5px/1.62 var(--serif);margin:0 0 16px;max-width:33em}
.prose p.links{font:15px var(--sans)}
.prs{border-collapse:collapse;width:100%}
.prs caption{text-align:left;font-size:14px;font-weight:620;padding-bottom:8px}
.prs th,.prs td{padding:10px 0;border-top:1px solid var(--rule);text-align:left;font-weight:400}
.prs th{color:var(--sec);font-size:15px}.prs td{text-align:right;font:500 24px/1.2 var(--serif)}
.prs tr:last-child>*{border-bottom:1px solid var(--rule)}
.gallery{display:grid;grid-template-columns:repeat(6,1fr);grid-auto-rows:120px;gap:10px;margin-top:56px}
.ph{margin:0;overflow:hidden;border-radius:3px;background:var(--soft)}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph.tall{grid-column:span 2;grid-row:span 3}.ph.wide{grid-column:span 4;grid-row:span 2}.ph.sq{grid-column:span 2}
.ph.slot{background:none;border:1.5px dashed var(--rule);display:flex;align-items:center;justify-content:center;color:var(--mute);font:12px var(--mono)}

footer{margin-top:120px;border-top:1px solid var(--rule)}
footer .in{max-width:1080px;margin:0 auto;padding:24px 40px 44px;display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;font-size:14px;color:var(--sec)}
footer a{color:var(--ink)}
@media (max-width:880px){
  main,.top .in,footer .in{padding-left:20px;padding-right:20px}
  .top nav{gap:16px}.dd .menu{display:none}
  h1{font-size:46px}.stage{height:300px}.reel [role=tab]{padding-right:10px;font-size:13.5px}.hero,.phead{padding-top:48px}.lede{font-size:18px}
  section{grid-template-columns:1fr;margin-top:64px}section>h2{margin-bottom:12px}
  .results th{font-size:20px;padding-right:16px}
  .work .card,.cols,.pair,.about{grid-template-columns:1fr;gap:20px}
  .proj header h2{font-size:34px}
  .win .bar .meta{display:none}.win .body{padding:16px}
  .gallery{grid-template-columns:repeat(2,1fr)}.ph.tall{grid-column:span 1;grid-row:span 2}.ph.wide{grid-column:span 2}.ph.sq{grid-column:span 1}
}
"""

# ------------------------------------------------------------------------------------------------ chart engine
JS = r"""
(() => {
const root = document.documentElement, FIG = JSON.parse(document.getElementById('figdata').textContent || '{}');
const css = k => getComputedStyle(root).getPropertyValue(k).trim();
const btn = document.getElementById('theme');
const isDark = () => root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
const lab = () => { btn.textContent = isDark() ? 'Light' : 'Dark'; };
btn.onclick = () => { root.dataset.theme = isDark() ? 'light' : 'dark'; try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {} lab(); redrawAll(); };
lab(); matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { lab(); redrawAll(); });

const fmt = (v, d = 0) => v == null || isNaN(v) ? '–' : Number(v).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const dlabel = s => { const [y, m, d] = s.split('-'); return `${MON[+m - 1]} ${+d}, ${y}`; };
const qlabel = q => `${q.slice(0, 4)} Q${q.slice(-1)}`;
const ts = s => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +(s.slice(8, 10) || 1));
function nice(lo, hi, n = 5) {
  const span = hi - lo || 1, step0 = span / n, p = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(x => x * p).find(s => span / s <= n) || 10 * p;
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, t = [];
  for (let v = a; v <= b + step / 2; v += step) t.push(+v.toFixed(10));
  return { lo: a, hi: b, ticks: t };
}

const charts = [];
function chart(el, draw, hover) {
  const c = document.createElement('canvas'); el.appendChild(c);
  const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; el.appendChild(tip);
  const ch = { el, c, tip, draw, hover, mx: null, reveal: 1 }; el._chart = ch;
  ch.render = () => {
    const dpr = window.devicePixelRatio || 1, W = el.clientWidth, H = el.clientHeight;
    c.width = W * dpr; c.height = H * dpr; const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H); g.font = `12px ${css('--sans')}`; g.textBaseline = 'middle';
    ch.geo = draw(g, W, H);
    if (ch.reveal < 1) {
      const P = ch.geo ? (ch.geo.f ? ch.geo.f.P : ch.geo.P) : null, pl = P ? P.l : 0, pr = P ? P.r : 0, pb = P ? P.b : 0;
      const x = pl + (W - pl - pr) * ch.reveal;
      g.fillStyle = css('--bg'); g.fillRect(x, 0, W - x, H - pb);
      g.fillStyle = css('--c1'); g.fillRect(Math.round(x), 0, 1.5, H - pb);
    }
    if (ch.mx != null && hover) { const r = hover(g, ch.geo, ch.mx, ch.my, W, H); if (r) { tip.innerHTML = r.html; tip.style.left = r.x + 'px'; tip.style.top = r.y + 'px'; tip.hidden = false; } else tip.hidden = true; }
    else tip.hidden = true;
  };
  const move = e => { const r = c.getBoundingClientRect(), p = e.touches ? e.touches[0] : e; ch.mx = p.clientX - r.left; ch.my = p.clientY - r.top; ch.render(); };
  c.addEventListener('mousemove', move); c.addEventListener('touchmove', move, { passive: true });
  c.addEventListener('mouseleave', () => { ch.mx = null; ch.render(); });
  new ResizeObserver(() => ch.render()).observe(el);
  charts.push(ch); return ch;
}
function redrawAll() { charts.forEach(c => c.render()); }
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
function sweep(ch, ms = 1300) {
  if (!ch || reduced) { if (ch) { ch.reveal = 1; ch.render(); } return; }
  const t0 = performance.now();
  const step = now => { const k = Math.min(1, (now - t0) / ms); ch.reveal = 1 - Math.pow(1 - k, 3); ch.render(); if (k < 1) requestAnimationFrame(step); };
  ch.reveal = 0; requestAnimationFrame(step);
}

// shared axes: x is a numeric domain, y linear
function frame(g, W, H, o) {
  const P = { l: o.padL ?? 52, r: o.padR ?? 12, t: o.padT ?? 10, b: o.padB ?? 26 };
  const X = v => P.l + (v - o.x0) / (o.x1 - o.x0) * (W - P.l - P.r);
  const Y = v => P.t + (o.y.hi - v) / (o.y.hi - o.y.lo) * (H - P.t - P.b);
  g.strokeStyle = css('--grid'); g.lineWidth = 1; g.fillStyle = css('--mute'); g.textAlign = 'right';
  if (!o.noYAxis) o.y.ticks.forEach(t => { const y = Math.round(Y(t)) + .5; g.beginPath(); g.moveTo(P.l, y); g.lineTo(W - P.r, y); g.stroke(); g.fillText(o.yfmt ? o.yfmt(t) : fmt(t), P.l - 8, y); });
  g.strokeStyle = css('--rule'); g.beginPath(); g.moveTo(P.l, Math.round(Y(o.y.lo)) + .5); g.lineTo(W - P.r, Math.round(Y(o.y.lo)) + .5); g.stroke();
  g.textAlign = 'center'; g.fillStyle = css('--mute');
  (o.xticks || []).forEach(([v, s]) => { const x = X(v); if (x < P.l - 1 || x > W - P.r + 1) return; g.fillText(s, x, H - P.b + 15); g.strokeStyle = css('--rule'); g.beginPath(); g.moveTo(Math.round(x) + .5, Y(o.y.lo)); g.lineTo(Math.round(x) + .5, Y(o.y.lo) + 4); g.stroke(); });
  return { P, X, Y, W, H };
}
const line = (g, pts, color, w = 1.6, dash) => { g.strokeStyle = color; g.lineWidth = w; g.setLineDash(dash || []); g.lineJoin = 'round'; g.beginPath(); let on = false; pts.forEach(([x, y]) => { if (y == null || isNaN(y)) { on = false; return; } on ? g.lineTo(x, y) : g.moveTo(x, y); on = true; }); g.stroke(); g.setLineDash([]); };
const cross = (g, f, x) => { g.strokeStyle = css('--mute'); g.lineWidth = 1; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(Math.round(x) + .5, f.P.t); g.lineTo(Math.round(x) + .5, f.Y(f.y0 ?? 0) || f.H - f.P.b); g.stroke(); g.setLineDash([]); };
const yearTicks = (a, b, step = 1) => { const out = []; for (let y = new Date(a).getUTCFullYear() + 1; y <= new Date(b).getUTCFullYear(); y += step) out.push([Date.UTC(y, 0, 1), String(y)]); return out; };

// ---------------------------------------------------------------- figures
const F = {};
F.hero = el => {
  const d = FIG.hero, t = d.dates.map(ts), x0 = t[0], x1 = t[t.length - 1];
  const sorted = [...d.raw].sort((a, b) => a - b), ylo = sorted[Math.floor(sorted.length * .002)], yhi = sorted[Math.floor(sorted.length * .996)];
  const y = nice(Math.min(ylo, ...d.adj) * .97, yhi);
  const fit = d.fit.map(([s, v]) => [ts(s), v]), brk = ts(d.brk);
  chart(el, (g, W, H) => {
    const f = frame(g, W, H, { x0, x1, y, xticks: yearTicks(x0, x1), yfmt: v => fmt(v), padL: 52, padT: 24 }); g.fillStyle = css('--mute'); g.textAlign = 'left'; g.fillText('MW', 4, 8);
    g.fillStyle = css('--mute'); g.globalAlpha = isDark() ? .5 : .38;
    d.raw.forEach((v, i) => { if (v > y.hi) return; g.fillRect(f.X(t[i]) - .9, f.Y(v) - .9, 1.8, 1.8); }); g.globalAlpha = 1;
    line(g, t.map((v, i) => [f.X(v), f.Y(d.adj[i])]), css('--c1'), 2);
    line(g, fit.map(([a, b]) => [f.X(a), f.Y(b)]), css('--ink'), 1.3, [5, 4]);
    const bx = f.X(brk); g.strokeStyle = css('--sec'); g.lineWidth = 1; g.beginPath(); g.moveTo(bx + .5, f.P.t + 4); g.lineTo(bx + .5, f.Y(y.lo)); g.stroke();
    g.fillStyle = css('--ink'); g.textAlign = 'right'; g.font = `500 12.5px ${css('--sans')}`;
    g.fillText(`+${fmt(d.s1)} MW/yr`, bx - 10, f.P.t + 14);
    g.textAlign = 'left'; g.fillText(`+${fmt(d.s2)} MW/yr`, bx + 10, f.P.t + 14);
    g.font = `12px ${css('--sans')}`; g.fillStyle = css('--sec'); g.fillText('trend break', bx + 10, f.P.t + 31);
    return { f, t };
  }, (g, { f, t }, mx) => {
    const v = t[0] + (mx - f.P.l) / (f.W - f.P.l - f.P.r) * (t[t.length - 1] - t[0]);
    let i = Math.max(0, Math.min(t.length - 1, Math.round((v - t[0]) / 864e5))); while (i > 0 && t[i] > v + 864e5) i--;
    const x = f.X(t[i]); cross(g, { ...f, y0: y.lo }, x);
    g.fillStyle = css('--c1'); g.beginPath(); g.arc(x, f.Y(d.adj[i]), 3.5, 0, 7); g.fill();
    return { x, y: f.Y(Math.max(d.adj[i], Math.min(d.raw[i], y.hi))), html: `<b>${dlabel(d.dates[i])}</b><br>Daily ${fmt(d.raw[i])} MW<br>Weather-adjusted ${fmt(d.adj[i])} MW` };
  });
};

function stackBars(el, d, mini) {
  const n = d.q.length, keys = Object.keys(d.stack), cols = ['--c1', '--c2', '--c3', '--c4'];
  const tot = d.q.map((_, i) => keys.reduce((s, k) => s + d.stack[k][i], 0));
  const y = nice(0, Math.max(...tot, ...(d.nvda || []).filter(v => v)) * 1.02, mini ? 3 : 5);
  chart(el, (g, W, H) => {
    const xt = d.q.map((q, i) => [i + .5, q.slice(0, 4)]).filter((_, i) => d.q[i].endsWith('Q1') && (mini || W < 640 ? +d.q[i].slice(0, 4) % 2 === 1 : true));
    const f = frame(g, W, H, { x0: 0, x1: n, y, xticks: xt, yfmt: v => '$' + fmt(v) + (v === y.ticks[y.ticks.length - 1] ? 'B' : ''), padL: mini ? 44 : 56, padR: mini ? 4 : 92 });
    const bw = (f.X(1) - f.X(0)), gap = Math.min(2, bw * .2);
    d.q.forEach((q, i) => { let acc = 0; keys.forEach((k, j) => { const v = d.stack[k][i]; g.fillStyle = css(cols[j]); g.fillRect(f.X(i) + gap / 2, f.Y(acc + v), bw - gap, f.Y(acc) - f.Y(acc + v) - (j ? .8 : 0)); acc += v; }); });
    if (!mini && d.nvda) {
      line(g, d.nvda.map((v, i) => [f.X(i + .5), v == null ? null : f.Y(v)]), css('--ink'), 1.8);
      const li = d.nvda.length - 1; g.fillStyle = css('--ink'); g.textAlign = 'right'; g.font = `600 12px ${css('--sans')}`; g.fillText('NVIDIA revenue', f.X(li + .5) - 10, f.Y(d.nvda[li]) - 12); g.textAlign = 'left';
      // direct labels on the last stack
      let acc = 0; g.font = `12px ${css('--sans')}`;
      keys.forEach((k, j) => { const v = d.stack[k][n - 1]; g.fillStyle = css('--sec'); g.fillText(d.names[j], f.X(n) + 8, f.Y(acc + v / 2) + (j === 3 ? 0 : 0)); acc += v; });
    }
    return { f, bw };
  }, (g, { f }, mx) => {
    const i = Math.floor((mx - f.P.l) / (f.X(1) - f.X(0))); if (i < 0 || i >= n) return null;
    g.fillStyle = css('--ink'); g.globalAlpha = .08; g.fillRect(f.X(i), f.P.t, f.X(1) - f.X(0), f.Y(0) - f.P.t); g.globalAlpha = 1;
    const rows = keys.map((k, j) => `${d.names[j]} $${fmt(d.stack[k][i], 1)}B`).join('<br>');
    return { x: f.X(i + .5), y: f.Y(tot[i]), html: `<b>${qlabel(d.q[i])}: $${fmt(tot[i], 1)}B capex</b>${mini ? '' : '<br>' + rows + (d.nvda && d.nvda[i] ? `<br>NVIDIA revenue $${fmt(d.nvda[i], 1)}B` : '')}` };
  });
}
F.capex = el => stackBars(el, FIG.capex, false);
F.capex_mini = el => stackBars(el, FIG.capex, true);

function multiLine(el, xs, series, o) {
  const t = xs.map(ts), all = series.flatMap(s => s.v.filter(v => v != null));
  const y = nice(Math.min(...all, o.floor ?? Infinity), Math.max(...all), 5);
  const cols = ['--c1', '--c2', '--c3', '--c4', '--c5'];
  chart(el, (g, W, H) => {
    const short = t[t.length - 1] - t[0] < 2.2 * 365 * 864e5;
    const xt = short ? xs.map((s, i) => [t[i], MON[+s.slice(5, 7) - 1] + (s.slice(5, 7) === '01' ? ' ' + s.slice(2, 4) : '')]).filter((_, i) => i % 2 === 0) : yearTicks(t[0], t[t.length - 1], o.ystep || 1);
    const f = frame(g, W, H, { x0: t[0], x1: t[t.length - 1], y, xticks: xt, yfmt: o.yfmt, padR: o.padR ?? 124, padL: o.padL ?? 44 });
    if (o.ref != null) { g.strokeStyle = css('--mute'); g.setLineDash([2, 3]); g.beginPath(); g.moveTo(f.P.l, f.Y(o.ref) + .5); g.lineTo(f.W - f.P.r, f.Y(o.ref) + .5); g.stroke(); g.setLineDash([]); }
    const ends = [];
    series.forEach((s, j) => {
      line(g, s.v.map((v, i) => [f.X(t[i]), v == null ? null : f.Y(v)]), css(s.color || cols[j]), j === 0 ? 2.2 : 1.6);
      let li = s.v.length - 1; while (li > 0 && s.v[li] == null) li--; ends.push({ y: f.Y(s.v[li]), name: s.name, c: s.color || cols[j], v: s.v[li] });
    });
    ends.sort((a, b) => a.y - b.y); for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 14) ends[k].y = ends[k - 1].y + 14;
    g.textAlign = 'left'; ends.forEach(e => { g.fillStyle = css(e.c); g.fillRect(f.W - f.P.r + 8, e.y - 1, 8, 2); g.fillStyle = css('--ink'); g.font = `12px ${css('--sans')}`; g.fillText(e.name, f.W - f.P.r + 20, e.y); });
    return { f, t };
  }, (g, { f, t }, mx) => {
    const v = t[0] + (mx - f.P.l) / (f.W - f.P.l - f.P.r) * (t[t.length - 1] - t[0]);
    let i = 0; t.forEach((tt, k) => { if (Math.abs(tt - v) < Math.abs(t[i] - v)) i = k; });
    const x = f.X(t[i]); cross(g, { ...f, y0: y.lo }, x);
    series.forEach((s, j) => { if (s.v[i] == null) return; g.fillStyle = css(s.color || cols[j]); g.beginPath(); g.arc(x, f.Y(s.v[i]), 3, 0, 7); g.fill(); });
    return { x, y: f.P.t + 8, html: `<b>${o.xlab(xs[i])}</b><br>` + series.filter(s => s.v[i] != null).map(s => `${s.name} ${o.vfmt(s.v[i])}`).join('<br>') };
  });
}
F.zones = el => { const d = FIG.zones; multiLine(el, d.months.map(m => m + '-15'), d.series, { ref: 100, xlab: s => `${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}`, vfmt: v => fmt(v, 1), ystep: 2 }); };

F.leadlag = el => {
  const d = FIG.leadlag, n = d.length, y = nice(Math.min(0, ...d.map(r => r.r), ...d.map(r => -r.band)), Math.max(...d.map(r => r.r), ...d.map(r => r.band)), 4);
  chart(el, (g, W, H) => {
    const f = frame(g, W, H, { x0: 0, x1: n, y, xticks: d.map((r, i) => [i + .5, (r.lag > 0 ? '+' : '') + r.lag]), yfmt: v => fmt(v, 1), padL: 40, padR: 8 });
    g.fillStyle = css('--mute'); g.globalAlpha = .14;
    d.forEach((r, i) => g.fillRect(f.X(i), f.Y(r.band), f.X(1) - f.X(0), f.Y(-r.band) - f.Y(r.band))); g.globalAlpha = 1;
    const best = d.reduce((a, b) => b.r > a.r ? b : a);
    d.forEach((r, i) => { g.fillStyle = r === best ? css('--c1') : css('--sec'); const bw = (f.X(1) - f.X(0)) * .46; const y0 = f.Y(0), y1 = f.Y(r.r); g.fillRect(f.X(i + .5) - bw / 2, Math.min(y0, y1), bw, Math.abs(y1 - y0)); });
    g.strokeStyle = css('--ink'); g.beginPath(); g.moveTo(f.P.l, Math.round(f.Y(0)) + .5); g.lineTo(f.W - f.P.r, Math.round(f.Y(0)) + .5); g.stroke();
    return { f };
  }, (g, { f }, mx) => {
    const i = Math.floor((mx - f.P.l) / (f.X(1) - f.X(0))); if (i < 0 || i >= n) return null; const r = d[i];
    return { x: f.X(i + .5), y: f.Y(Math.max(r.r, r.band)), html: `<b>k = ${r.lag > 0 ? '+' : ''}${r.lag}</b><br>r = ${fmt(r.r, 3)}, n = ${r.n}<br>noise band &plusmn;${fmt(r.band, 3)}` };
  });
};

// ---------------------------------------------------------------- Sundown (live from status.json)
let SD = null; const sdWait = [];
fetch('/sundown/status.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null).then(s => { SD = s; sdWait.forEach(f => f()); }).catch(() => {});
const whenSD = fn => SD ? fn() : sdWait.push(fn);
// diverging ramps (bg -> light -> mid -> deep); charging and discharging each get their own hue
const RAMPS = {
  light: { neg: ['#ffffff', '#c9dcf4', '#5b95dd', '#1f5fbf', '#0b3170'], pos: ['#ffffff', '#fbd6bb', '#f08a4b', '#d9480f', '#7f2604'] },
  dark:  { neg: ['#101214', '#14273f', '#2a5ea5', '#5a9be8', '#bcd8fb'], pos: ['#101214', '#3b1d0f', '#a8431a', '#e8692f', '#ffc9a3'] },
};
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
function ramp(stops, a) {
  const x = Math.max(0, Math.min(1, a)) * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), t = x - i;
  const p = hex(stops[i]), q = hex(stops[i + 1]); return [0, 1, 2].map(k => Math.round(p[k] + (q[k] - p[k]) * t));
}
function heat(el, region, mini) {
  const st = { region }; let cache = null;
  const ch = chart(el, (g, W, H) => {
    const h = SD && SD.charts && SD.charts.heatmap[st.region]; if (!h) { g.fillStyle = css('--mute'); g.fillText('Loading live data…', 8, H / 2); return null; }
    const z = h.z, nw = z.length, legend = !mini && W > 420;
    const P = mini ? { l: 0, r: 0, t: 0, b: 0 } : { l: 64, r: 4, t: 4, b: legend ? 50 : 24 };
    const pw = W - P.l - P.r, ph = H - P.t - P.b, cw = pw / nw, rh = ph / 24;
    const abs = z.flat().filter(v => v != null).map(Math.abs).sort((a, b) => a - b), mx = abs[Math.floor(abs.length * .97)] || 1;
    const R = RAMPS[isDark() ? 'dark' : 'light'], key = st.region + (isDark() ? 'd' : 'l');
    if (!cache || cache.key !== key) {
      // one pixel per (week, hour), smoothed when scaled up: reads like a spectrogram, not a grid of tiles
      const off = document.createElement('canvas'); off.width = nw; off.height = 24; const ox = off.getContext('2d'), im = ox.createImageData(nw, 24);
      for (let w = 0; w < nw; w++) for (let hr = 0; hr < 24; hr++) {
        const v = z[w][hr] ?? 0, c = ramp(v >= 0 ? R.pos : R.neg, Math.pow(Math.abs(v) / mx, .75)), o = (hr * nw + w) * 4;
        im.data[o] = c[0]; im.data[o + 1] = c[1]; im.data[o + 2] = c[2]; im.data[o + 3] = 255;
      }
      ox.putImageData(im, 0, 0); cache = { key, off };
    }
    g.save(); g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
    if (!mini) { g.beginPath(); g.roundRect ? g.roundRect(P.l, P.t, pw, ph, 3) : g.rect(P.l, P.t, pw, ph); g.clip(); }
    g.drawImage(cache.off, 0, 0, nw, 24, P.l - cw / 2, P.t - rh / 2, pw + cw, ph + rh); g.restore();
    if (!mini) {
      // quiet hour guides at 6 am, noon, 6 pm
      g.strokeStyle = isDark() ? 'rgba(255,255,255,.18)' : 'rgba(0,0,0,.14)'; g.lineWidth = 1; g.setLineDash([2, 4]);
      [6, 12, 18].forEach(hr => { const y = Math.round(P.t + hr * rh) + .5; g.beginPath(); g.moveTo(P.l, y); g.lineTo(P.l + pw, y); g.stroke(); }); g.setLineDash([]);
      g.fillStyle = css('--mute'); g.textAlign = 'right';
      [[0, 'midnight'], [6, '6 am'], [12, 'noon'], [18, '6 pm']].forEach(([hr, s]) => g.fillText(s, P.l - 10, P.t + hr * rh + (hr ? 0 : 7)));
      g.textAlign = 'center'; const every = cw * 4.35 < 34 ? 2 : 1;
      h.weeks.forEach((wk, w) => { if (wk.slice(8, 10) <= '07' && (+wk.slice(5, 7)) % every === 0) g.fillText(MON[+wk.slice(5, 7) - 1], P.l + (w + .5) * cw, P.t + ph + 14); });
      if (legend) {
        const ly = H - 9, bw = Math.min(200, pw * .32), half = bw / 2;
        const lt = 'Charging', rt = `Discharging   scale \u00b1${fmt(mx)} MW`;
        const rw = g.measureText(rt).width, x1 = P.l + pw - rw - 10 - bw, x0 = x1 - g.measureText(lt).width - 10;
        g.fillStyle = css('--sec'); g.textAlign = 'left'; g.fillText(lt, x0, ly); g.fillText(rt, x1 + bw + 10, ly);
        for (let k = 0; k < bw; k++) { const t = (k - half) / half, c = ramp(t >= 0 ? R.pos : R.neg, Math.pow(Math.abs(t), .75)); g.fillStyle = `rgb(${c})`; g.fillRect(x1 + k, ly - 4, 1.2, 8); }
        g.strokeStyle = css('--ink'); g.lineWidth = 1; g.beginPath(); g.moveTo(x1 + half + .5, ly - 7); g.lineTo(x1 + half + .5, ly + 7); g.stroke();
      }
    }
    return { P, cw, rh, h, W, H };
  }, mini ? null : (g, geo, mx, my) => {
    if (!geo) return null; const w = Math.floor((mx - geo.P.l) / geo.cw), hr = Math.floor((my - geo.P.t) / geo.rh);
    if (w < 0 || hr < 0 || w >= geo.h.z.length || hr > 23) return null; const v = geo.h.z[w][hr];
    const x = geo.P.l + (w + .5) * geo.cw, y = geo.P.t + (hr + .5) * geo.rh;
    g.strokeStyle = css('--ink'); g.lineWidth = 1.5; g.beginPath(); g.arc(x, y, 5, 0, 7); g.stroke();
    return { x, y: y - 6, html: `<b>Week of ${dlabel(geo.h.weeks[w])}, ${hr}:00</b><br>${v >= 0 ? 'Discharging' : 'Charging'} ${fmt(Math.abs(v))} MW` };
  });
  whenSD(() => ch.render());
  return { ch, st };
}
F.heat = el => {
  const h = heat(el, 'CISO');
  const seg = el.parentElement.querySelectorAll('[data-region]');
  seg.forEach(b => b.onclick = () => { seg.forEach(x => x.setAttribute('aria-checked', x === b)); h.st.region = b.dataset.region; h.ch.render(); });
};
F.heat_mini = el => heat(el, 'CISO', true);
F.duck = el => {
  const ch = chart(el, (g, W, H) => {
    const d = SD && SD.charts && SD.charts.duck.CISO; if (!d) return null;
    const y = nice(Math.min(0, ...d.battery) * 1.1, Math.max(...d.demand) * 1.05, 5);
    const f = frame(g, W, H, { x0: 0, x1: 24, y, xticks: [[0.5, 'midnight'], [6.5, '6 am'], [12.5, 'noon'], [18.5, '6 pm']], yfmt: v => fmt(v), padL: 36, padR: 8 });
    g.fillStyle = css('--c4'); g.globalAlpha = .22; g.beginPath(); g.moveTo(f.X(.5), f.Y(0)); d.solar.forEach((v, i) => g.lineTo(f.X(i + .5), f.Y(Math.max(0, v)))); g.lineTo(f.X(23.5), f.Y(0)); g.fill(); g.globalAlpha = 1;
    const bw = (f.X(1) - f.X(0)) * .62;
    d.battery.forEach((v, i) => { g.fillStyle = v >= 0 ? css('--pos') : css('--neg'); g.fillRect(f.X(i + .5) - bw / 2, Math.min(f.Y(0), f.Y(v)), bw, Math.abs(f.Y(v) - f.Y(0))); });
    line(g, d.demand.map((v, i) => [f.X(i + .5), f.Y(v)]), css('--ink'), 1.8);
    g.fillStyle = css('--sec'); g.textAlign = 'left'; g.fillText('demand', f.X(1), f.Y(d.demand[1]) - 10);
    const si = d.solar.indexOf(Math.max(...d.solar)); g.fillText('solar', f.X(si + .5) - 14, f.Y(d.solar[si] / 2));
    return { f, d };
  }, (g, geo, mx) => {
    if (!geo) return null; const { f, d } = geo, i = Math.floor((mx - f.P.l) / (f.X(1) - f.X(0))); if (i < 0 || i > 23) return null;
    cross(g, { ...f, y0: 0 }, f.X(i + .5));
    return { x: f.X(i + .5), y: f.P.t + 6, html: `<b>${i}:00</b><br>Demand ${fmt(d.demand[i], 1)} GW<br>Solar ${fmt(d.solar[i], 1)} GW<br>Batteries ${d.battery[i] >= 0 ? '+' : ''}${fmt(d.battery[i], 1)} GW` };
  });
  whenSD(() => ch.render());
};
F.net = el => {
  const ch = chart(el, (g, W, H) => {
    const n = SD && SD.charts && SD.charts.net; if (!n || !n.day) return null;
    const t = n.day.map(ts), v = n.share_7d, y = nice(0, Math.max(...v.filter(x => x != null)) * 1.08, 4);
    const xt = n.day.map((s, i) => [t[i], MON[+s.slice(5, 7) - 1]]).filter(([_, __], i) => n.day[i].slice(8, 10) === '01' && +n.day[i].slice(5, 7) % 2 === 1);
    const f = frame(g, W, H, { x0: t[0], x1: t[t.length - 1], y, xticks: xt, yfmt: x => fmt(x) + '%', padL: 40, padR: 8 });
    g.fillStyle = css('--c1'); g.globalAlpha = .12; g.beginPath(); g.moveTo(f.X(t[0]), f.Y(0)); v.forEach((x, i) => x != null && g.lineTo(f.X(t[i]), f.Y(x))); g.lineTo(f.X(t[t.length - 1]), f.Y(0)); g.fill(); g.globalAlpha = 1;
    line(g, v.map((x, i) => [f.X(t[i]), x == null ? null : f.Y(x)]), css('--c1'), 2);
    return { f, t, v, days: n.day };
  }, (g, geo, mx) => {
    if (!geo) return null; const { f, t, v, days } = geo; const tv = t[0] + (mx - f.P.l) / (f.W - f.P.l - f.P.r) * (t[t.length - 1] - t[0]);
    const i = Math.max(0, Math.min(t.length - 1, Math.round((tv - t[0]) / 864e5))); if (v[i] == null) return null;
    cross(g, { ...f, y0: 0 }, f.X(t[i])); g.fillStyle = css('--c1'); g.beginPath(); g.arc(f.X(t[i]), f.Y(v[i]), 3.5, 0, 7); g.fill();
    return { x: f.X(t[i]), y: f.Y(v[i]), html: `<b>${dlabel(days[i])}</b><br>${fmt(v[i], 1)}% of net peak` };
  });
  whenSD(() => ch.render());
};
F.monthly = el => {
  const names = { CISO: 'California', ERCO: 'Texas', ISNE: 'New England', MISO: 'Midwest', SWPP: 'Great Plains', NYIS: 'New York', PJM: 'Mid-Atlantic' };
  let built = false;
  whenSD(() => {
    const m = SD && SD.charts && SD.charts.monthly; if (!m || built) return; built = true;
    const months = [...new Set(m.month)].sort();
    const regs = [...new Set(m.region_code)].map(r => ({ r, v: months.map(mo => { const i = m.month.findIndex((x, k) => x === mo && m.region_code[k] === r); return i < 0 ? null : m.discharge_mwh[i] / 1e3; }) }))
      .sort((a, b) => Math.max(...b.v.filter(x => x != null)) - Math.max(...a.v.filter(x => x != null)));
    const full = months.slice(1, -1);
    multiLine(el, full.map(x => x + '-15'), regs.slice(0, 5).map(s => ({ name: names[s.r] || s.r, v: s.v.slice(1, -1) })),
      { xlab: s => `${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}`, vfmt: v => fmt(v) + ' GWh', yfmt: v => fmt(v), padL: 48, padR: 110, floor: 0 });
  });
};

document.querySelectorAll('[data-fig]').forEach(el => { const f = F[el.dataset.fig]; if (f) f(el); });
if (document.fonts) document.fonts.ready.then(redrawAll);

// home reel: rotate figures, sweep each in, swap the caption; pause on hover, focus or button
document.querySelectorAll('[data-reel]').forEach(reel => {
  const tabs = [...reel.querySelectorAll('[role=tab]')], slides = [...reel.querySelectorAll('.slide')];
  const bar = reel.querySelector('.prog i'), cap = reel.querySelector('figcaption .t'), num = reel.querySelector('figcaption .n'), pauseBtn = reel.querySelector('.pause');
  const DWELL = 7000; let i = 0, timer = null, userPaused = reduced, hovering = false;
  const show = (k, focus) => {
    i = (k + slides.length) % slides.length;
    tabs.forEach((t, j) => { const on = j === i; t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1; slides[j].classList.toggle('on', on); slides[j].setAttribute('aria-hidden', !on); });
    cap.innerHTML = tabs[i].dataset.cap; num.textContent = i + 1;
    sweep(slides[i].querySelector('.plot')._chart);
    if (focus) tabs[i].focus();
    arm();
  };
  const arm = () => {
    clearTimeout(timer); bar.style.transition = 'none'; bar.style.width = '0';
    if (userPaused || hovering) return;
    void bar.offsetWidth; bar.style.transition = `width ${DWELL}ms linear`; bar.style.width = '100%';
    timer = setTimeout(() => show(i + 1), DWELL);
  };
  tabs.forEach((t, j) => { t.onclick = () => show(j); t.onkeydown = e => { const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (d) { e.preventDefault(); show(i + d, true); } }; });
  pauseBtn.onclick = () => { userPaused = !userPaused; pauseBtn.setAttribute('aria-pressed', userPaused); pauseBtn.textContent = userPaused ? 'Play' : 'Pause'; arm(); };
  if (reduced) { pauseBtn.textContent = 'Play'; pauseBtn.setAttribute('aria-pressed', 'true'); }
  reel.addEventListener('mouseenter', () => { hovering = true; arm(); });
  reel.addEventListener('mouseleave', () => { hovering = false; arm(); });
  reel.addEventListener('focusin', () => { hovering = true; arm(); });
  reel.addEventListener('focusout', e => { if (!reel.contains(e.relatedTarget)) { hovering = false; arm(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(timer); else arm(); });
  const first = slides[0].querySelector('.plot')._chart; if (!reduced && first) first.reveal = 0; (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => sweep(first, 1800));
  arm();
});

// tabs, as-of toggles, sticky header, live status
document.querySelectorAll('[data-win]').forEach(win => {
  const tabs = [...win.querySelectorAll('[role=tab]')], meta = win.querySelector('.bar .meta');
  const pick = (t, focus) => { tabs.forEach(x => { const on = x === t; x.setAttribute('aria-selected', on); x.tabIndex = on ? 0 : -1; const p = document.getElementById(x.getAttribute('aria-controls')); p.hidden = !on; if (on && meta) meta.textContent = p.dataset.meta || ''; }); if (focus) t.focus(); };
  tabs.forEach((t, i) => { t.onclick = () => pick(t); t.onkeydown = e => { const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (d) { e.preventDefault(); pick(tabs[(i + d + tabs.length) % tabs.length], true); } }; });
});
document.querySelectorAll('.ask').forEach(box => { const bs = box.querySelectorAll('[data-asof]'); bs.forEach(b => b.onclick = () => { bs.forEach(x => x.setAttribute('aria-checked', x === b)); box.querySelectorAll('[data-ans]').forEach(a => a.hidden = a.dataset.ans !== b.dataset.asof); }); });
const $$ = k => document.querySelectorAll(`[data-live="${k}"]`);
const set = (k, v) => $$(k).forEach(e => e.textContent = v);
const ago = s => { const t = Date.parse(String(s).replace(' UTC', 'Z').replace(' ', 'T')); if (!t) return null; const h = (Date.now() - t) / 36e5; return { h, text: h < 1 ? 'Updated under an hour ago' : h < 48 ? `Updated ${Math.round(h)} hours ago` : `Updated ${Math.round(h / 24)} days ago` }; };
if ($$('gl-when').length) {
  fetch('/gigawatt-ledger/data/manifest.json', { cache: 'no-store' }).then(r => r.ok && r.json()).then(m => { const a = m && ago(m.built_at); if (a) { set('gl-when', a.text); $$('gl-dot').forEach(d => d.classList.toggle('ok', a.h < 36)); } }).catch(() => {});
  fetch('/gigawatt-ledger/data/history.csv', { cache: 'no-store' }).then(r => r.ok && r.text()).then(t => { if (!t) return; const v = {}; t.trim().split('\n').slice(1).forEach(l => { const [, k, x] = l.split(','); v[k] = x; }); if (v.grid_hours && v.sec_data_points) set('gl-what', `${fmt(v.grid_hours)} grid hours and ${fmt(v.sec_data_points)} SEC data points.`); }).catch(() => {});
}
whenSD(() => { if (!SD) return; if (SD.rows) set('sd-rows', fmt(SD.rows)); if (SD.ciso_battery_share_30d != null) set('sd-share', fmt(SD.ciso_battery_share_30d, 1) + '%'); });
})();
"""


# Logo: a U whose right side rises straight into the A; the crossbar carries the accent colour.
_UA = ("M9 12 V33 C9 45 15 51 23 51 C31 51 35 45 37 37 L45 12 L58.4 56", "M41.2 32.5 H48.4")
_G = ("<g transform='translate(7 7.5) scale(.78)' stroke-width='5.4' fill='none' stroke-linejoin='miter' stroke-miterlimit='3'>"
      "<clipPath id='uaclip'><rect x='0' y='0' width='64' height='53.7'/></clipPath>"
      "<path d='{a}' stroke='{fg}' clip-path='url(#uaclip)'/><path d='{b}' stroke='{acc}'/></g>")


def logo_svg(bg, fg, acc):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='{bg}'/>"
            + _G.format(a=_UA[0], b=_UA[1], fg=fg, acc=acc) + "</svg>")


MARK = logo_svg("currentColor", "var(--bg)", "var(--c1)").replace("<svg xmlns='http://www.w3.org/2000/svg'", "<svg aria-hidden='true'")
FAVICON = "data:image/svg+xml," + logo_svg("#141619", "#ffffff", "#e8622c").replace("<", "%3C").replace(">", "%3E").replace("#", "%23").replace(" ", "%20")


def layout(title, desc, body, page, figdata):
    cur = lambda p: ' aria-current="page"' if p == page else ""
    data = json.dumps(figdata, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#101214" media="(prefers-color-scheme: dark)">
<link rel="icon" href="{FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400..700;1,8..60,400;1,8..60,500&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script>try{{const t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<style>{CSS}</style>
</head>
<body>
<header class="top"><div class="in">
  <a class="me" href="/" aria-label="{PROFILE['name']}, home">{MARK}</a>
  <nav><a href="/"{cur('home')}>Home</a><div class="dd"><a href="/projects/"{cur('projects')} aria-haspopup="true">Projects</a><div class="menu">
    <a href="/projects/#gigawatt-ledger"><b>Gigawatt Ledger</b><span>AI spending from filings to the power grid</span></a>
    <a href="/projects/#sundown"><b>Sundown</b><span>Batteries on U.S. grids after sunset</span></a>
    <a href="/projects/#bmw"><b>BMW engine tuning</b><span>125 calibration parameters, 6 modules</span></a>
    <p><a href="/gigawatt-ledger/">Gigawatt Ledger dashboard</a><a href="/sundown/">Sundown dashboard</a></p>
  </div></div><a href="/about/"{cur('about')}>About</a><a href="mailto:{PROFILE['email']}">Email</a><button id="theme" type="button">Dark</button></nav>
</div></header>
<main>{body}
</main>
<footer><div class="in">
  <span><a href="mailto:{PROFILE['email']}">{PROFILE['email']}</a> &nbsp; <a href="{PROFILE['linkedin']}">LinkedIn</a></span>
  <span>Figures drawn in the browser from my pipelines&rsquo; data: U.S. EIA, SEC EDGAR, Open-Meteo.</span>
</div></footer>
<script id="figdata" type="application/json">{data}</script>
<script>{JS}</script>
</body>
</html>
"""


# =============================================================================================== build
def build() -> Path:
    H, L = hero_series(), latest()
    pub = HERE / ("preview" if PREVIEW else "public")
    if pub.exists():
        shutil.rmtree(pub)
    (pub / "projects").mkdir(parents=True)
    (pub / "about").mkdir()
    (pub / "index.html").write_text(home(H, L))
    (pub / "projects" / "index.html").write_text(projects_page(H, L))
    (pub / "about" / "index.html").write_text(about_page())
    photos = [p for p in (HERE / "photos").glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    if photos:
        (pub / "photos").mkdir()
        for p in photos:
            shutil.copy2(p, pub / "photos" / p.name)
    (pub / "README.md").write_text("Personal site of Utkarsh Agarwal. These pages are built and published automatically.\n")
    redirect = ('<!DOCTYPE html><meta charset="utf-8"><title>Moved</title><meta http-equiv="refresh" content="0; url={u}">'
                '<link rel="canonical" href="{u}"><a href="{u}">This page moved here.</a>')
    for old, new in [("follow-the-power", "/gigawatt-ledger/"), ("grid-pulse", "/sundown/")]:
        (pub / old).mkdir(parents=True, exist_ok=True)
        (pub / old / "index.html").write_text(redirect.format(u=new))
    return pub


if __name__ == "__main__":
    print(build())
