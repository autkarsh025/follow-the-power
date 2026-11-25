"""Build Utkarsh Agarwal's personal site into site/public/ (home, /projects/, /about/).

The public site (utkarshagarwal.dev) holds only built pages; this script stays private.
Figures come from the Gigawatt Ledger open dataset (docs/data) at build time; the status readout
refreshes in the browser from each pipeline's published files. Snippets are real: code excerpts
from the two projects and answers the AI analyst gave in its eval runs (site/snippets.json).

Usage:
  python site/build_site.py            # publishable build -> site/public/
  python site/build_site.py --preview  # also shows empty photo slots and fill-in prompts -> site/preview/
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "docs" / "data"
FALLBACK = json.loads((HERE / "figdata.json").read_text())
SNIPPETS = json.loads((HERE / "snippets.json").read_text())
HYPERSCALERS = {"MSFT", "GOOGL", "AMZN", "META"}
PREVIEW = "--preview" in sys.argv
esc = html.escape

# =============================================================================================== content
# Everything here comes from the résumé or the projects themselves. The phone number stays off the site.
PROFILE = {
    "name": "Utkarsh Agarwal",
    "email": "autkarsh@berkeley.edu",
    "linkedin": "https://www.linkedin.com/in/utkarsh-agarwal-4078601b1/",
}

EXPERIENCE = [
    ("Jan 2025 – May 2025", "Undergraduate data researcher", "University of Wisconsin–Madison",
     "Analyzed dorm energy data in Python with faculty and PhD researchers and found about 12% in potential savings. "
     "Mapped shuttle ridership to flag inefficient routes and presented recommendations to faculty."),
    ("May 2023 – Aug 2023", "Software engineering intern", "Seaside Sustainability, Gloucester, MA",
     "Built a full-stack Python and Node.js web app with backend services and an integrated online donation system."),
]
LEADERSHIP = [
    ("May 2025 – now", "Director of Internal Development, Project Lead", "Project Rishi at UC Berkeley",
     "Lead a 10-person team in a 40-member organization building a rural educational coaching center, "
     "including grant applications and budget planning through UC Berkeley."),
    ("Aug 2023 – May 2024", "Division I cross country and track", "University of Wisconsin–Madison",
     "Ran DI cross country and track after captaining my high school varsity team as its top runner."),
]
EDUCATION = [
    ("Expected May 2028", "B.A. Data Science and Applied Mathematics", "University of California, Berkeley",
     "Admitted as an out-of-state transfer, about 40 a year. Data structures, computer architecture, data science, "
     "linear algebra, discrete math."),
    ("Aug 2023 – May 2025", "B.S. Data Science and Economics, transferred", "University of Wisconsin–Madison",
     "Data science programming I and II, object-oriented programming, microeconomic theory, econometrics."),
]
SKILLS = [
    ("Languages", "Python, SQL, R, Java, C, Swift"),
    ("AI and ML", "LLM agents and tool use, agent skills, RAG, eval harnesses, MCP, Claude and OpenAI APIs, PyTorch"),
    ("Data engineering", "pandas, SQLite, Spark, Airflow, Git, Docker, GitHub Actions, pytest, Plotly"),
]

# Photos for /about/: drop the file in site/photos/ and it appears. Missing files are skipped
# (shown as labelled empty slots only in --preview builds).
PHOTOS = [
    ("me.jpg", "Portrait", "tall"),
    ("running.jpg", "Racing for UW–Madison", "wide"),
    ("bmw.jpg", "The BMW", "square"),
    ("berkeley.jpg", "Berkeley", "square"),
]
# Prompts for things only Utkarsh can write. Rendered only in --preview builds.
FILL_IN = [
    ("Where I'm from", "Where you grew up and anything about it you'd want someone to know."),
    ("Running", "Favourite race, a PR you're proud of, whether you still run and where."),
    ("The car", "Which BMW, why you started tuning it, the best part of the project."),
    ("Lately", "Books, music, a sport, anything you're into right now."),
]


# =============================================================================================== data
def _rows(name):
    p = DATA / name
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def load_series():
    dom = [(r["month"], float(r["normalized_mw"])) for r in _rows("zone_weather_normalized.csv")
           if r["zone"] == "DOM" and r["normalized_mw"]]
    cap = defaultdict(dict)
    for r in _rows("fin_quarterly.csv"):
        if r["metric"] == "capex" and r["ticker"] in HYPERSCALERS:
            cap[r["quarter"]][r["ticker"]] = float(r["value_usd"])
    capq = sorted((q, sum(v.values()) / 1e9) for q, v in cap.items() if len(v) == len(HYPERSCALERS))
    latest = {r["metric"]: float(r["value"]) for r in _rows("history.csv")}
    if len(dom) < 24 or len(capq) < 8:
        dom = list(zip(FALLBACK["dom_m"], FALLBACK["dom"]))
        capq = list(zip(FALLBACK["cap_q"], FALLBACK["cap"]))
    capq = [x for x in capq if x[0] >= "2019Q1"]
    return dom, capq, latest


def code_excerpt(path: Path, start: str, stop: str | None = None, max_lines: int = 26) -> str:
    """Lines of a source file from the line containing `start` up to (not including) `stop`."""
    lines = path.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if start in l)
    j = next((n for n in range(i + 1, len(lines)) if stop and stop in lines[n]), len(lines))
    out = lines[i:min(j, i + max_lines)]
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _snippet(cache: str, src: Path, start: str, stop: str | None, n: int = 26) -> str:
    """Excerpt from the source when it is available (local builds), else the cached copy (CI)."""
    cached = HERE / cache
    if src.exists():
        cached.write_text(re.sub(r"\n{3,}", "\n\n", code_excerpt(src, start, stop, n)) + "\n")
    return cached.read_text().rstrip("\n") if cached.exists() else ""


def sql_snippet():
    return _snippet("snippet_netpeak.sql", ROOT.parent / "gp-repo" / "sql" / "analysis" / "06_net_peak_battery_share.sql",
                    "daily_net_peak AS (", None)


def py_snippet():
    return _snippet("snippet_verify.py", ROOT / "ftp" / "verify.py", "def matches(", "def provenance(")


# =============================================================================================== highlighting
SQL_KW = r"\b(WITH|AS|SELECT|FROM|WHERE|AND|OR|JOIN|USING|ON|GROUP|BY|ORDER|DESC|ASC|OVER|PARTITION|CASE|WHEN|THEN|ELSE|END|SUM|COUNT|ROUND|NULLIF|COALESCE|ROW_NUMBER|IS|NOT|NULL)\b"
PY_KW = r"\b(def|return|for|in|if|not|any|max|abs|import|from|as|True|False|None|and|or|with)\b"


def highlight(code: str, lang: str) -> str:
    """Tiny tokenizer: comments, strings, numbers, keywords. Enough for short snippets."""
    kw = SQL_KW if lang == "sql" else PY_KW
    com = r"--[^\n]*" if lang == "sql" else r"#[^\n]*"
    pat = re.compile(rf'(?P<c>{com})|(?P<s>"""[\s\S]*?"""|\'[^\'\n]*\'|"[^"\n]*")|(?P<n>\b\d+(?:\.\d+)?(?:e-?\d+)?\b)|(?P<k>{kw})')
    out, pos = [], 0
    for m in pat.finditer(code):
        out.append(esc(code[pos:m.start()]))
        out.append(f'<span class="h{m.lastgroup}">{esc(m.group())}</span>')
        pos = m.end()
    out.append(esc(code[pos:]))
    return "".join(out)


def md_inline(text: str) -> str:
    """Render the analyst's markdown (bold, bullets, tables) into simple HTML."""
    out, in_ul, table = [], False, []

    def inline(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s))

    def flush_table():
        nonlocal table
        if table:
            rows_ = [r for r in table if not re.match(r"^\|[\s|:-]+\|$", r)]
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows_]
            head, body = cells[0], cells[1:]
            out.append('<table class="ans"><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            table = []

    for line in text.strip().splitlines():
        s = line.strip()
        if s.startswith("|"):
            table.append(s)
            continue
        flush_table()
        if s.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(s[2:])}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if s:
            out.append(f"<p>{inline(s)}</p>")
    flush_table()
    if in_ul:
        out.append("</ul>")
    return "".join(out)


# =============================================================================================== figures
def trace(dom, W=1000, H=150):
    vals = [v for _, v in dom]
    lo, hi = min(vals), max(vals)
    pad = 6
    x = lambda i: W * i / (len(vals) - 1)
    pts = " ".join(f"{x(i):.1f},{pad + (H - 2 * pad) * (hi - v) / (hi - lo):.1f}" for i, v in enumerate(vals))
    ticks = "".join(f'<text x="{x(i) + 4:.1f}" y="{H + 18}" class="tk">{m[:4]}</text>'
                    f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{H + 4}" y2="{H + 8}" class="tl"/>'
                    for i, (m, _) in enumerate(dom) if m.endswith("-01"))
    return (f'<svg class="trace" viewBox="0 0 {W} {H + 24}" preserveAspectRatio="none" role="img" '
            f'aria-label="Northern Virginia weather-adjusted electricity load, {dom[0][1]:,.0f} MW in {dom[0][0][:4]} to {dom[-1][1]:,.0f} MW in {dom[-1][0]}">'
            f'<line x1="0" x2="{W}" y1="{H + 4}" y2="{H + 4}" class="tl"/>{ticks}'
            f'<polyline points="{pts}" class="line" pathLength="1"/></svg>')


def capex_chart(capq, W=640, H=150):
    vals = [v for _, v in capq]
    mx = max(vals) * 1.04
    bw = W / len(vals)
    bars = "".join(
        f'<rect x="{i * bw + 1:.2f}" y="{H - H * v / mx:.1f}" width="{bw - 2:.2f}" height="{H * v / mx:.1f}" '
        f'class="{"hi" if i == len(vals) - 1 else "b"}" data-tip="{q[:4]} Q{q[-1]}: ${v:.1f}B"/>'
        for i, (q, v) in enumerate(capq))
    ticks = "".join(f'<text x="{i * bw:.1f}" y="{H + 16}" class="tk">{q[:4]}</text>'
                    for i, (q, _) in enumerate(capq) if q.endswith("Q1") and int(q[:4]) % 2 == 1)
    return (f'<svg class="chart" viewBox="0 0 {W} {H + 20}" preserveAspectRatio="none" role="img" '
            f'aria-label="Big tech capital spending per quarter, rising to ${vals[-1]:.0f} billion">{bars}{ticks}</svg>')


def duck_chart(W=640, H=150):
    bat = FALLBACK["duck"]["battery"]
    lo, hi = min(min(bat), 0) * 1.08, max(bat) * 1.08
    y = lambda v: H * (hi - v) / (hi - lo)
    bw = W / 24
    hour = lambda i: "midnight" if i == 0 else "noon" if i == 12 else f"{i % 12} {'am' if i < 12 else 'pm'}"
    bars = []
    for i, v in enumerate(bat):
        y0, y1 = sorted([y(0), y(v)])
        verb = "discharging" if v > 0 else "charging"
        bars.append(f'<rect x="{i * bw + 2:.1f}" y="{y0:.1f}" width="{bw - 4:.1f}" height="{max(y1 - y0, 1):.1f}" '
                    f'class="{"hi" if v > 0 else "b"}" data-tip="{hour(i)}: {verb} {abs(v):.1f} GW"/>')
    ticks = "".join(f'<text x="{(h + .5) * bw:.1f}" y="{H + 16}" text-anchor="middle" class="tk">{hour(h)}</text>' for h in (0, 6, 12, 18))
    return (f'<svg class="chart" viewBox="0 0 {W} {H + 20}" preserveAspectRatio="none" role="img" '
            f'aria-label="California batteries charge at midday and discharge after sunset">'
            f'<line x1="0" x2="{W}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zl"/>{"".join(bars)}{ticks}</svg>')


# =============================================================================================== components
def rows(items):
    return "".join(
        f'<li class="row"><span class="when">{esc(w)}</span><div><h3>{esc(t)}</h3>'
        f'<p class="org">{esc(o)}</p><p>{esc(d)}</p></div></li>' for w, t, o, d in items)


def window(wid: str, tabs: list[tuple[str, str, str]]) -> str:
    """A tabbed snippet window. tabs = [(label, meta line, html body)]."""
    bar = "".join(f'<button role="tab" id="{wid}-t{i}" aria-controls="{wid}-p{i}" aria-selected="{str(i == 0).lower()}" '
                  f'tabindex="{0 if i == 0 else -1}">{esc(lab)}</button>' for i, (lab, _, _) in enumerate(tabs))
    panels = "".join(f'<div role="tabpanel" id="{wid}-p{i}" aria-labelledby="{wid}-t{i}" {"hidden" if i else ""} data-meta="{esc(meta)}">{body}</div>'
                     for i, (_, meta, body) in enumerate(tabs))
    return (f'<div class="win" data-win><div class="bar"><div role="tablist" aria-label="Snippets">{bar}</div>'
            f'<span class="meta">{esc(tabs[0][1])}</span></div><div class="body">{panels}</div></div>')


def code_block(code: str, lang: str) -> str:
    return f'<pre class="code"><code>{highlight(code, lang)}</code></pre>'


def ask_panel() -> str:
    a, b = SNIPPETS["time_travel_2025Q1"], SNIPPETS["time_travel_2025Q2"]
    ans = lambda c: md_inline(c["answer"].split("\n\nThe data show")[0])
    return (f'<div class="ask"><p class="q">{esc(a["question"])}</p>'
            f'<div class="asof" role="radiogroup" aria-label="Ask as of"><span>Ask as of</span>'
            f'<button role="radio" aria-checked="true" data-asof="0">{a["as_of"]}</button>'
            f'<button role="radio" aria-checked="false" data-asof="1">{b["as_of"]}</button></div>'
            f'<div class="a" data-ans="0">{ans(a)}</div><div class="a" data-ans="1" hidden>{ans(b)}</div>'
            f'<p class="note">Real answers from the analyst&rsquo;s eval runs. Same question, different date: it can only see '
            f'what had been filed by then, so it can&rsquo;t cheat with hindsight.</p></div>')


def ratio_panel() -> str:
    c = SNIPPETS["multi_step_ratio"]
    body = c["answer"].split("\n\nThe ratio dipped")[0]
    return (f'<div class="ask"><p class="q">{esc(c["question"])}</p><div class="a">{md_inline(body)}</div>'
            f'<p class="note">A real answer from an eval run. Every figure in it was checked against the data before it was shown.</p></div>')


def chart_panel(svg: str, caption: str) -> str:
    return f'<figure class="live">{svg}<figcaption><span data-tipout>{esc(caption)}</span></figcaption></figure>'


# =============================================================================================== pages
INDEX = [
    ("2026", "Gigawatt Ledger", "#gigawatt-ledger", "AI spending from big tech to the power grid, with an AI analyst", "Python, SQL, Claude API", "/gigawatt-ledger/"),
    ("2026", "Sundown", "#sundown", "How batteries carry U.S. grids after sunset", "Python, SQL, Airflow, Spark", "/sundown/"),
    ("2026", "BMW engine and electronics tuning", "#bmw", "125 calibration parameters, 6 control modules", "C, Python", ""),
    ("2025", "Campus energy research", "/#experience", "Dorm energy and shuttle ridership at UW–Madison", "Python", ""),
    ("2023", "Donation web app", "/#experience", "Full-stack app for Seaside Sustainability", "Python, Node.js", ""),
]


def index_table():
    body = "".join(
        f'<tr><td class="y">{y}</td><td class="n"><a href="{a}">{esc(n)}</a></td><td class="d">{esc(d)}</td>'
        f'<td class="s">{esc(s)}</td><td class="l">{f"<a href={chr(34)}{l}{chr(34)}>Live</a>" if l else ""}</td></tr>'
        for y, n, a, d, s, l in INDEX)
    return (f'<table class="index"><thead><tr><th>Year</th><th>Project</th><th class="d">What</th><th class="s">Built with</th><th></th></tr></thead>'
            f'<tbody>{body}</tbody></table>')


def project_cases(dom, capq, latest):
    cap_now = latest.get("hyperscaler_capex_b", capq[-1][1])
    cap_yoy = latest.get("hyperscaler_capex_yoy")
    base = [v for m, v in dom if m.startswith(dom[0][0][:4])]
    dom_add = sum(v for _, v in dom[-12:]) / 12 - sum(base) / len(base)
    q = capq[-1][0]
    gl = window("gl", [
        ("Chart", "live, from the project's open dataset",
         chart_panel(capex_chart(capq), f"Big tech capex per quarter, 2019 to {q[:4]} Q{q[-1]}. Hover a bar.")),
        ("Time travel", "AI analyst, point-in-time data", ask_panel()),
        ("Multi-step", "AI analyst, calculation", ratio_panel()),
        ("Code", "ftp/verify.py", code_block(py_snippet(), "py")),
    ])
    sd = window("sd", [
        ("Chart", "California, an average day",
         chart_panel(duck_chart(), "Batteries charge on midday solar and discharge after sunset. Hover a bar.")),
        ("SQL", "sql/analysis/06_net_peak_battery_share.sql", code_block(sql_snippet(), "sql")),
    ])
    bmw_fig = ('<figure class="photo"><img src="/photos/bmw.jpg" alt="The BMW" loading="lazy"></figure>' if (HERE / "photos" / "bmw.jpg").exists()
               else ('<div class="slot banner">Photo of the car goes here<small>site/photos/bmw.jpg</small></div>' if PREVIEW else ""))
    return f"""
    <article class="case" id="gigawatt-ledger">
      <aside class="meta">
        <p class="yr">2026 – now</p>
        <dl><dt>Role</dt><dd>Solo project</dd><dt>Built with</dt><dd>Python, SQL, Claude API, RAG, MCP, eval harness, GitHub Actions</dd></dl>
        <p class="go"><a href="/gigawatt-ledger/">Open the dashboard</a><a href="/gigawatt-ledger/data/README.md">Open dataset</a></p>
      </aside>
      <div class="main">
        <h2>Gigawatt Ledger</h2>
        <p class="sub">Tracks AI spending from big tech&rsquo;s filings to the power grid</p>
        <p>A daily pipeline joins SEC filings from 26 companies across the AI supply chain (cloud buyers, chips, servers,
        power equipment, data-center landlords and utilities) with hourly electricity demand in five data-center regions,
        weather removed. Every number keeps the date it became public, so any past day can be replayed without hindsight.</p>
        <p>An AI analyst answers questions from the data and 4,600+ filing passages through 16 tools. Code checks every
        number it writes before you see it, and an eval suite of 22 cases, prompt injection included, runs it end to end.</p>
        {gl}
        <dl class="nums">
          <div><dt>Big tech capex, {q[:4]} Q{q[-1]}</dt><dd>${cap_now:.1f}B{f'<small>up {cap_yoy:.0f}% in a year</small>' if cap_yoy else ''}</dd></div>
          <div><dt>Always-on load added in N. Virginia since 2019</dt><dd>+{dom_add:,.0f} MW<small>weather removed</small></dd></div>
          <div><dt>Eval cases passed</dt><dd>22 of 22<small>three runs each</small></dd></div>
        </dl>
      </div>
    </article>

    <article class="case" id="sundown">
      <aside class="meta">
        <p class="yr">2026 – now</p>
        <dl><dt>Role</dt><dd>Solo project</dd><dt>Built with</dt><dd>Python, SQL, Airflow, Spark, pytest, Plotly</dd></dl>
        <p class="go"><a href="/sundown/">Open the dashboard</a></p>
      </aside>
      <div class="main">
        <h2>Sundown</h2>
        <p class="sub">How batteries carry U.S. grids through the evening peak</p>
        <p>Every morning a pipeline pulls hourly data for the seven major U.S. grids into SQL with Airflow and Spark,
        checks it, and rebuilds a live dashboard. Batteries now cover about a quarter of California&rsquo;s evening peak.</p>
        <p>The quality checks and 59 tests caught 3 errors in the government&rsquo;s own source data. The carbon analysis
        shows midday EV charging in California emits about 60% less CO&#8322; than overnight.</p>
        {sd}
        <dl class="nums">
          <div><dt>California peak from batteries, last 30 days</dt><dd data-live="sd-share">25.4%</dd></div>
          <div><dt>Hourly records validated</dt><dd data-live="sd-rows">587,104</dd></div>
          <div><dt>Errors caught in source data</dt><dd>3</dd></div>
        </dl>
      </div>
    </article>

    <article class="case" id="bmw">
      <aside class="meta">
        <p class="yr">May – Aug 2026</p>
        <dl><dt>Role</dt><dd>Personal project</dd><dt>Built with</dt><dd>C, Python, ECU calibration</dd></dl>
      </aside>
      <div class="main">
        <h2>BMW engine and electronics tuning</h2>
        <p class="sub">Reprogramming a car&rsquo;s computers by hand</p>
        <p>Reprogrammed the engine control unit in C and Python, tuning 125 calibration parameters across ignition timing,
        fuel delivery, boost and throttle response. Reconfigured 6 electronic control modules to add real-time G-force
        telemetry and radar displays to the car&rsquo;s own screens.</p>
        {bmw_fig}
        <dl class="nums">
          <div><dt>Calibration parameters tuned</dt><dd>125</dd></div>
          <div><dt>Engine performance</dt><dd>+17%</dd></div>
          <div><dt>Control modules reconfigured</dt><dd>6</dd></div>
        </dl>
      </div>
    </article>"""


def home(dom, capq, latest):
    grid_hours, sec_points = latest.get("grid_hours"), latest.get("sec_data_points")
    sel = "".join(
        f'<li><a class="sel" href="/projects/{a}"><span class="sn">{esc(n)}</span><span class="sd">{esc(d)}</span><span class="sy">{y}</span></a></li>'
        for y, n, a, d, _, _ in INDEX[:3])
    readout = f"""
    <table class="status" aria-label="Pipeline status">
      <tr><th scope="row"><a href="/gigawatt-ledger/">gigawatt-ledger</a></th><td><i class="dot" data-live="gl-dot"></i><span data-live="gl-when">runs daily</span></td>
          <td data-live="gl-what">{f"{grid_hours:,.0f} grid hours, {sec_points:,.0f} SEC data points" if grid_hours and sec_points else "SEC filings and hourly grid data"}</td></tr>
      <tr><th scope="row"><a href="/sundown/">sundown</a></th><td><i class="dot" data-live="sd-dot"></i><span data-live="sd-when">runs daily</span></td>
          <td data-live="sd-what">hourly data for 7 U.S. grids</td></tr>
    </table>"""
    body = f"""
  <div class="hero">
    <h1>{PROFILE['name']}</h1>
    <p class="lede">I study <b>data science and applied math at UC Berkeley</b> and build data pipelines and AI tools on public energy and financial data. Two of them are running right now.</p>
    <p class="contact"><a href="mailto:{PROFILE['email']}">{PROFILE['email']}</a><a href="{PROFILE['linkedin']}">LinkedIn</a></p>
    {trace(dom)}
    <div class="tcap"><span>Northern Virginia&rsquo;s always-on electricity load, weather removed, monthly since {dom[0][0][:4]}</span><span><b>{dom[0][1]:,.0f} MW</b> to <b>{dom[-1][1]:,.0f} MW</b></span></div>
    {readout}
  </div>

  <section id="work"><h2>Projects</h2><div>
    <ul class="selected">{sel}</ul>
    <p class="more"><a href="/projects/">All projects, with live data and code</a></p>
  </div></section>

  <section class="about"><h2>About</h2><div>
    <p>I like problems where the data is public but nobody has lined it up yet. My two main projects follow money and electricity: one traces AI spending from big tech&rsquo;s filings down to the power grid, the other measures how much batteries carry U.S. grids after sunset.</p>
    <p>I transferred to Berkeley from UW&ndash;Madison, where I ran Division I cross country and track. Outside of code I work on cars.</p>
    <p class="more"><a href="/about/">More about me</a></p>
  </div></section>

  <section id="experience"><h2>Experience</h2><ul class="list">{rows(EXPERIENCE)}</ul></section>
  <section><h2>Leadership and athletics</h2><ul class="list">{rows(LEADERSHIP)}</ul></section>
  <section><h2>Education</h2><ul class="list">{rows(EDUCATION)}</ul></section>
  <section><h2>Skills</h2><dl class="skills">{"".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in SKILLS)}</dl></section>"""
    return layout(PROFILE["name"], "Data science and applied math at UC Berkeley. Data pipelines and AI tools on public energy and financial data.", body, "home")


def projects_page(dom, capq, latest):
    body = f"""
  <div class="phead">
    <h1>Projects</h1>
    <p class="lede">Things I&rsquo;ve built. The two data projects run every day on their own. Each one below has its live data, real output from the system and a piece of its code.</p>
  </div>
  {index_table()}
  {project_cases(dom, capq, latest)}"""
    return layout(f"Projects | {PROFILE['name']}", "Gigawatt Ledger, Sundown and other projects by Utkarsh Agarwal, with live data and code.", body, "projects")


def about_page():
    have = [(f, c, s) for f, c, s in PHOTOS if (HERE / "photos" / f).exists()]
    tiles = [f'<figure class="ph {s}"><img src="/photos/{f}" alt="{esc(c)}" loading="lazy"><figcaption>{esc(c)}</figcaption></figure>' for f, c, s in have]
    if PREVIEW:
        tiles += [f'<div class="ph {s} slot"><span>{esc(c)}</span><small>site/photos/{f}</small></div>' for f, c, s in PHOTOS if (f, c, s) not in have]
    gallery = f'<div class="gallery">{"".join(tiles)}</div>' if tiles else ""
    prompts = "".join(f'<div class="fill"><h3>{esc(t)}</h3><p>{esc(p)}</p></div>' for t, p in FILL_IN) if PREVIEW else ""
    body = f"""
  <div class="phead">
    <h1>About</h1>
    <p class="lede">I&rsquo;m Utkarsh. I study data science and applied math at UC Berkeley and expect to graduate in May 2028.</p>
  </div>
  {gallery}
  <section><h2>Background</h2><div class="prose">
    <p>I started college at UW&ndash;Madison in data science and economics, where I ran Division I cross country and track and worked
    with faculty and PhD researchers on campus energy use. In 2025 I transferred to Berkeley as an out-of-state transfer admit,
    one of about 40 a year.</p>
    <p>Most of what I build sits where data meets the physical world: power grids, batteries, data centers, engines. I like
    projects that keep running after I stop typing, and numbers I can check against the source.</p>
  </div></section>
  <section><h2>Now</h2><ul class="now">
    <li><b>Building</b> Gigawatt Ledger and Sundown, which refresh themselves every morning.</li>
    <li><b>Leading</b> internal development for Project Rishi, a 40-member Berkeley group building a rural educational coaching center.</li>
    <li><b>Studying</b> data science and applied math at Berkeley.</li>
    <li><b>Looking</b> for 2027 internships in data and AI engineering.</li>
  </ul></section>
  <section><h2>Running</h2><div class="prose">
    <p>I captained my high school varsity team as its top runner, then ran Division I cross country and track at UW&ndash;Madison.</p>
  </div></section>
  <section><h2>Cars</h2><div class="prose">
    <p>I tune my BMW myself: I reprogrammed its engine computer, adjusting 125 calibration parameters, and added live
    G-force telemetry and radar displays to its screens. More on the <a href="/projects/#bmw">projects page</a>.</p>
  </div></section>
  {f'<section><h2>To fill in</h2><div class="fills">{prompts}</div></section>' if prompts else ''}
  <section><h2>Contact</h2><div class="prose">
    <p>The best way to reach me is <a href="mailto:{PROFILE['email']}">{PROFILE['email']}</a>, or on <a href="{PROFILE['linkedin']}">LinkedIn</a>.</p>
  </div></section>"""
    return layout(f"About | {PROFILE['name']}", "About Utkarsh Agarwal: data science and applied math at UC Berkeley, Division I runner, car tinkerer.", body, "about")


# =============================================================================================== layout
CSS = r"""
:root{
  --bg:#ffffff;--ink:#15171b;--sec:#585e67;--mute:#8a9098;--rule:#e6e8eb;--panel:#f7f8fa;--panel2:#f0f2f5;
  --accent:#2a4fd6;--bar:#cdd6f6;--ok:#1f8a4c;--kw:#2a4fd6;--str:#2c7a4b;--num:#b4561b;--com:#8a9098;
  --sans:"Schibsted Grotesk",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace;
}
:root[data-theme=dark]{--bg:#0e1013;--ink:#e8eaee;--sec:#a4aab3;--mute:#737983;--rule:#23272e;--panel:#14171c;--panel2:#1a1e24;--accent:#8ea5ff;--bar:#2c3a68;--ok:#4cc27e;--kw:#8ea5ff;--str:#7fd1a0;--num:#f0a46b;--com:#6d737d}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e1013;--ink:#e8eaee;--sec:#a4aab3;--mute:#737983;--rule:#23272e;--panel:#14171c;--panel2:#1a1e24;--accent:#8ea5ff;--bar:#2c3a68;--ok:#4cc27e;--kw:#8ea5ff;--str:#7fd1a0;--num:#f0a46b;--com:#6d737d}}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:76px}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16.5px/1.62 var(--sans);-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums;text-rendering:optimizeLegibility}
::selection{background:color-mix(in srgb,var(--accent) 22%,transparent)}
a{color:inherit;text-decoration:underline;text-decoration-color:var(--rule);text-decoration-thickness:1.5px;text-underline-offset:3px;transition:text-decoration-color .15s,color .15s}
a:hover{text-decoration-color:var(--accent)}
a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
.wrap{max-width:1000px;margin:0 auto;padding:0 32px}

header.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:saturate(1.4) blur(10px);-webkit-backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid transparent;transition:border-color .2s}
header.top.scrolled{border-bottom-color:var(--rule)}
.top .wrap{display:flex;align-items:center;justify-content:space-between;height:62px}
.top .me{font-weight:650;text-decoration:none;letter-spacing:-.01em;display:flex;align-items:center;gap:10px}
.top .me svg{width:22px;height:22px;display:block;color:var(--ink)}
.top nav{display:flex;gap:26px;align-items:center;font-size:15px}
.top nav a{text-decoration:none;color:var(--sec);position:relative}
.top nav a:hover,.top nav a[aria-current=page]{color:var(--ink)}
.top nav a[aria-current=page]::after{content:"";position:absolute;left:0;right:0;bottom:-21px;height:2px;background:var(--ink)}
#theme{font:inherit;font-size:14px;color:var(--sec);background:none;border:1px solid var(--rule);border-radius:7px;padding:3px 11px;cursor:pointer}
#theme:hover{color:var(--ink);border-color:var(--mute)}

h1{font-size:clamp(44px,7.4vw,78px);line-height:.98;letter-spacing:-.045em;font-weight:720;margin:0}
.lede{font-size:21px;line-height:1.5;color:var(--sec);max-width:31em;margin:22px 0 0;letter-spacing:-.005em}
.lede b{color:var(--ink);font-weight:550}
.hero,.phead{padding:88px 0 0}
.contact{display:flex;gap:22px;margin-top:22px;font-size:15.5px}
.trace{display:block;width:100%;height:170px;margin-top:64px;overflow:visible}
.trace .line{fill:none;stroke:var(--accent);stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.trace .tl,.chart .zl{stroke:var(--rule);stroke-width:1;vector-effect:non-scaling-stroke}
svg .tk{fill:var(--mute);font:12px var(--sans)}
@media (prefers-reduced-motion:no-preference){.trace .line{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 2.2s cubic-bezier(.4,0,.2,1) .25s forwards}}
@keyframes draw{to{stroke-dashoffset:0}}
.tcap{display:flex;justify-content:space-between;gap:24px;font-size:14px;color:var(--sec);margin-top:10px}
.tcap b{color:var(--ink);font-weight:600}
.status{width:100%;border-collapse:collapse;margin-top:36px;font:14px/1.4 var(--mono);color:var(--sec);border-top:1px solid var(--rule)}
.status th,.status td{text-align:left;padding:11px 18px 11px 0;border-bottom:1px solid var(--rule);font-weight:400;white-space:nowrap}
.status th{color:var(--ink);font-weight:500;width:200px}
.status th a{text-decoration:none}.status th a:hover{text-decoration:underline}
.status td:last-child{white-space:normal;width:100%}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--mute);margin-right:9px;vertical-align:1px}
.dot.ok{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 22%,transparent)}

section{display:grid;grid-template-columns:200px 1fr;gap:0 36px;margin-top:104px}
section>h2{font-size:15px;font-weight:600;color:var(--sec);margin:4px 0 0;letter-spacing:0;position:sticky;top:88px;align-self:start}
.about p,.prose p{font-size:19px;line-height:1.6;margin:0 0 16px;max-width:34em;letter-spacing:-.005em}
.about p:last-child,.prose p:last-child{margin-bottom:0}
p.more{font-size:16px;font-weight:550;margin-top:22px}

.selected{list-style:none;margin:0;padding:0;border-top:1px solid var(--rule)}
.sel{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr) 60px;gap:24px;align-items:baseline;padding:18px 0;border-bottom:1px solid var(--rule);text-decoration:none}
.sel .sn{font-size:20px;font-weight:650;letter-spacing:-.02em;text-decoration:underline;text-decoration-color:transparent;text-decoration-thickness:2px;text-underline-offset:4px;transition:text-decoration-color .15s}
.sel .sd{color:var(--sec)}.sel .sy{color:var(--mute);text-align:right;font-size:14px}
.sel:hover .sn{text-decoration-color:var(--accent)}

.list{list-style:none;padding:0;margin:0}
.row{display:grid;grid-template-columns:1fr 170px;gap:24px;padding:0 0 26px;margin-bottom:26px;border-bottom:1px solid var(--rule)}
.row:last-child{border-bottom:0;margin-bottom:0;padding-bottom:0}
.row .when{order:2;text-align:right;font-size:14px;color:var(--mute);padding-top:3px}
.row h3{font-size:18px;font-weight:620;letter-spacing:-.012em;margin:0;line-height:1.35}
.row .org{color:var(--sec);margin:1px 0 8px}
.row p{margin:0;max-width:38em}
.skills{margin:0}
.skills div{display:grid;grid-template-columns:170px 1fr;gap:24px;padding:12px 0;border-bottom:1px solid var(--rule)}
.skills div:first-child{padding-top:0}.skills div:last-child{border-bottom:0}
.skills dt{font-weight:600}.skills dd{margin:0;color:var(--sec)}

.index{width:100%;border-collapse:collapse;margin-top:64px;font-size:15px}
.index th{text-align:left;font-weight:500;color:var(--mute);font-size:13.5px;padding:0 16px 10px 0;border-bottom:1px solid var(--rule)}
.index td{padding:14px 16px 14px 0;border-bottom:1px solid var(--rule);vertical-align:baseline}
.index .y{color:var(--mute);width:64px}
.index .n{font-weight:620;white-space:nowrap}.index .n a{text-decoration:none}.index .n a:hover{text-decoration:underline;text-decoration-color:var(--accent)}
.index td.d{color:var(--sec)}.index td.s{color:var(--mute);white-space:nowrap}
.index .l{text-align:right;padding-right:0;font-weight:550}

.case{display:grid;grid-template-columns:200px minmax(0,1fr);gap:0 36px;margin-top:120px;scroll-margin-top:80px}
.case .meta{position:sticky;top:88px;align-self:start;font-size:14.5px}
.case .yr{margin:8px 0 18px;color:var(--mute)}
.case .meta dl{margin:0}.case .meta dt{color:var(--mute);font-size:13px;margin-top:12px}.case .meta dd{margin:2px 0 0;color:var(--sec)}
.case .go{display:flex;flex-direction:column;gap:6px;margin-top:22px;font-weight:550}
.case h2{font-size:42px;line-height:1.05;letter-spacing:-.035em;font-weight:700;margin:0}
.case .sub{font-size:19px;color:var(--sec);margin:8px 0 20px}
.case .main>p{margin:0 0 14px;max-width:38em}

.win{margin:32px 0 8px;border:1px solid var(--rule);border-radius:12px;background:var(--panel);overflow:hidden}
.win .bar{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:0 16px 0 6px;border-bottom:1px solid var(--rule);background:var(--panel2)}
.win [role=tablist]{display:flex;overflow-x:auto;scrollbar-width:none}
.win [role=tab]{font:500 14px var(--sans);color:var(--sec);background:none;border:0;padding:12px 12px;cursor:pointer;position:relative;white-space:nowrap}
.win [role=tab]:hover,.win [role=tab][aria-selected=true]{color:var(--ink)}
.win [role=tab][aria-selected=true]::after{content:"";position:absolute;left:12px;right:12px;bottom:0;height:2px;background:var(--accent);border-radius:2px}
.win .bar .meta{font:12.5px var(--mono);color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.win .body{padding:24px 26px;min-height:268px}
@media (prefers-reduced-motion:no-preference){.win [role=tabpanel]{animation:fade .2s ease}}
@keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
.live{margin:0}.live svg{width:100%;height:196px;display:block;overflow:visible}
.chart .b{fill:var(--bar)}.chart .hi{fill:var(--accent)}
.chart rect{transition:opacity .12s;cursor:crosshair}.chart:hover rect{opacity:.5}.chart rect:hover{opacity:1}
.live figcaption{margin-top:16px;font:13px var(--mono);color:var(--sec);min-height:1.4em}
.code{margin:0;font:13px/1.7 var(--mono);color:var(--ink);overflow-x:auto;white-space:pre;tab-size:4}
.hc{color:var(--com);font-style:italic}.hs{color:var(--str)}.hn{color:var(--num)}.hk{color:var(--kw);font-weight:500}
.ask .q{margin:0 0 16px;font-weight:620;font-size:17px;letter-spacing:-.01em}
.asof{display:flex;align-items:center;gap:6px;margin:0 0 18px;font-size:13.5px;color:var(--mute)}
.asof span{margin-right:6px}
.asof button{font:500 13px var(--mono);color:var(--sec);background:var(--bg);border:1px solid var(--rule);border-radius:6px;padding:4px 9px;cursor:pointer;transition:background .15s,color .15s}
.asof button[aria-checked=true]{color:var(--bg);background:var(--ink);border-color:var(--ink)}
.ask .a{border-left:2px solid var(--accent);padding-left:16px;font-size:15.5px}
.ask .a p{margin:0 0 10px}.ask .a ul{margin:0 0 10px;padding-left:18px}.ask .a li{margin:3px 0}
.ask .note{font-size:13.5px;color:var(--mute);margin:16px 0 0;max-width:44em}
table.ans{border-collapse:collapse;margin:6px 0 12px;font-size:14.5px}
table.ans th,table.ans td{padding:4px 22px 4px 0;border-bottom:1px solid var(--rule);text-align:left}
table.ans th{font-weight:550;color:var(--sec)}

.nums{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:28px 0 0}
.nums div{border-top:2px solid var(--ink);padding-top:10px}
.nums dt{font-size:13.5px;color:var(--sec);line-height:1.35}
.nums dd{margin:4px 0 0;font-size:28px;font-weight:680;letter-spacing:-.03em;line-height:1.1;white-space:nowrap}
.nums small{display:block;font-size:13px;font-weight:450;color:var(--sec);letter-spacing:0;margin-top:3px}
.photo{margin:28px 0 0}.photo img{width:100%;display:block;border-radius:12px}
.slot{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;border:1.5px dashed var(--rule);border-radius:12px;color:var(--mute);font-size:14px;text-align:center;padding:20px}
.slot.banner{aspect-ratio:16/7;margin-top:28px}.slot small{font:12px var(--mono)}

.gallery{display:grid;grid-template-columns:repeat(6,1fr);grid-auto-rows:118px;gap:12px;margin-top:56px}
.ph{margin:0;position:relative;border-radius:12px;overflow:hidden;background:var(--panel)}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph figcaption{position:absolute;left:12px;bottom:10px;font-size:12.5px;color:#fff;text-shadow:0 1px 8px rgba(0,0,0,.55)}
.ph.tall{grid-column:span 2;grid-row:span 3}.ph.wide{grid-column:span 4;grid-row:span 2}.ph.square{grid-column:span 2;grid-row:span 1}
.ph.slot{background:none}
.now{list-style:none;margin:0;padding:0}
.now li{font-size:18px;padding:12px 0;border-bottom:1px solid var(--rule);max-width:36em}
.now li:first-child{padding-top:0}.now li:last-child{border-bottom:0}
.now b{font-weight:620;display:inline-block;min-width:88px}
.fills{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.fill{border:1.5px dashed var(--rule);border-radius:12px;padding:16px 18px}
.fill h3{margin:0 0 4px;font-size:16px}.fill p{margin:0;color:var(--mute);font-size:14.5px}

footer{margin:128px 0 0;padding:28px 0 48px;border-top:1px solid var(--rule)}
footer .wrap{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;font-size:14.5px;color:var(--sec)}
footer .say{font-size:28px;letter-spacing:-.03em;font-weight:680;color:var(--ink);margin:0 0 10px}
footer .say a{text-decoration-color:var(--accent)}
footer p{margin:0}

@media (max-width:860px){
  .wrap{padding:0 22px}
  .top .me span{display:none}
  .top nav{gap:18px}
  .hero,.phead{padding-top:56px}.lede{font-size:18.5px}
  .trace{height:120px;margin-top:44px}.trace .tk{display:none}
  section,.case{grid-template-columns:1fr;margin-top:76px}
  section>h2{position:static;margin-bottom:18px}
  .case .meta{position:static;order:2;margin-top:28px;padding-top:18px;border-top:1px solid var(--rule)}
  .case .meta .yr{margin:0 0 4px}
  .case .meta dl{display:grid;grid-template-columns:auto 1fr;gap:6px 16px}.case .meta dt,.case .meta dd{margin:0}
  .case .go{flex-direction:row;gap:20px;margin-top:16px}
  .live .tk{display:none}
  .case h2{font-size:32px}
  .row{grid-template-columns:1fr;gap:2px}.row .when{order:0;text-align:left}
  .skills div{grid-template-columns:1fr;gap:2px}
  .status th{width:auto}.status td:last-child{display:none}
  .tcap{flex-direction:column;gap:2px}
  .sel{grid-template-columns:1fr auto;gap:2px 12px}.sel .sd{grid-column:1/-1;grid-row:2}
  .index th.d,.index th.s,.index td.d,.index td.s{display:none}
  .win .bar .meta{display:none}.win .body{padding:18px 16px;min-height:0}.live svg{height:150px}
  .nums{grid-template-columns:1fr 1fr}
  .gallery{grid-template-columns:repeat(2,1fr);grid-auto-rows:110px}
  .ph.tall{grid-column:span 1;grid-row:span 2}.ph.wide{grid-column:span 2;grid-row:span 2}.ph.square{grid-column:span 1}
  .fills{grid-template-columns:1fr}
}
@media print{header.top,#theme{display:none}.trace .line{animation:none;stroke-dasharray:none}}
"""

JS = r"""
(() => {
  const root = document.documentElement, btn = document.getElementById('theme');
  const dark = () => root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const label = () => { btn.textContent = dark() ? 'Light' : 'Dark'; };
  btn.onclick = () => { root.dataset.theme = dark() ? 'light' : 'dark'; try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {} label(); };
  label();
  const top = document.querySelector('header.top');
  const onScroll = () => top.classList.toggle('scrolled', scrollY > 8);
  addEventListener('scroll', onScroll, { passive: true }); onScroll();

  document.querySelectorAll('[data-win]').forEach(win => {
    const tabs = [...win.querySelectorAll('[role=tab]')], meta = win.querySelector('.bar .meta');
    const pick = (t, focus) => {
      tabs.forEach(x => { const on = x === t; x.setAttribute('aria-selected', on); x.tabIndex = on ? 0 : -1;
        const p = document.getElementById(x.getAttribute('aria-controls')); p.hidden = !on; if (on && meta) meta.textContent = p.dataset.meta || ''; });
      if (focus) t.focus();
    };
    tabs.forEach((t, i) => {
      t.onclick = () => pick(t);
      t.onkeydown = e => { const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (d) { e.preventDefault(); pick(tabs[(i + d + tabs.length) % tabs.length], true); } };
    });
  });
  document.querySelectorAll('.ask').forEach(box => {
    const bs = box.querySelectorAll('[data-asof]');
    bs.forEach(b => b.onclick = () => {
      bs.forEach(x => x.setAttribute('aria-checked', x === b));
      box.querySelectorAll('[data-ans]').forEach(a => a.hidden = a.dataset.ans !== b.dataset.asof);
    });
  });
  document.querySelectorAll('figure.live').forEach(fig => {
    const out = fig.querySelector('[data-tipout]'); if (!out) return; const base = out.textContent;
    fig.querySelectorAll('[data-tip]').forEach(r => { r.onmouseenter = () => out.textContent = r.dataset.tip; });
    fig.querySelector('svg').onmouseleave = () => out.textContent = base;
  });

  const $$ = k => document.querySelector(`[data-live="${k}"]`);
  const ago = s => {
    const t = Date.parse(String(s).replace(' UTC', 'Z').replace(' ', 'T')); if (!t) return null;
    const h = (Date.now() - t) / 36e5;
    return { h, text: h < 1 ? 'updated under an hour ago' : h < 48 ? `updated ${Math.round(h)} hours ago` : `updated ${Math.round(h / 24)} days ago` };
  };
  const mark = (who, when) => { const a = when && ago(when), w = $$(who + '-when'), d = $$(who + '-dot'); if (!a || !w) return; w.textContent = a.text; d && d.classList.toggle('ok', a.h < 36); };
  const n = x => Number(x).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const set = (k, v) => { const e = $$(k); if (e) e.textContent = v; };
  if ($$('gl-when')) {
    fetch('/gigawatt-ledger/data/manifest.json', { cache: 'no-store' }).then(r => r.ok && r.json()).then(m => m && mark('gl', m.built_at)).catch(() => {});
    fetch('/gigawatt-ledger/data/history.csv', { cache: 'no-store' }).then(r => r.ok && r.text()).then(t => {
      if (!t) return; const v = {}; t.trim().split('\n').slice(1).forEach(l => { const [, k, x] = l.split(','); v[k] = x; });
      if (v.grid_hours && v.sec_data_points) set('gl-what', `${n(v.grid_hours)} grid hours, ${n(v.sec_data_points)} SEC data points`);
    }).catch(() => {});
  }
  if ($$('sd-when') || $$('sd-rows')) fetch('/sundown/status.json', { cache: 'no-store' }).then(r => r.ok && r.json()).then(s => {
    if (!s) return; mark('sd', s.built_at);
    if (s.rows) { set('sd-what', `${n(s.rows)} hourly records, ${s.grids || 7} grids`); set('sd-rows', n(s.rows)); }
    if (s.ciso_battery_share_30d != null) set('sd-share', s.ciso_battery_share_30d + '%');
  }).catch(() => {});
})();
"""

MARK = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="currentColor"/>'
        '<polyline points="6,22 11,19 15,21 20,13 26,9" fill="none" stroke="var(--bg)" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/></svg>')
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2315171b'/%3E"
           "%3Cpolyline points='6,22 11,19 15,21 20,13 26,9' fill='none' stroke='%23ffffff' stroke-width='2.6' stroke-linejoin='round' stroke-linecap='round'/%3E%3C/svg%3E")


def layout(title: str, desc: str, body: str, page: str) -> str:
    cur = lambda p: ' aria-current="page"' if p == page else ""
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
<meta name="theme-color" content="#0e1013" media="(prefers-color-scheme: dark)">
<link rel="icon" href="{FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400..800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script>try{{const t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<style>{CSS}</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="me" href="/" aria-label="{PROFILE['name']}, home">{MARK}<span>{PROFILE['name']}</span></a>
  <nav><a href="/projects/"{cur('projects')}>Projects</a><a href="/about/"{cur('about')}>About</a><a href="mailto:{PROFILE['email']}">Contact</a><button id="theme" type="button">Dark</button></nav>
</div></header>
<main class="wrap">{body}
</main>
<footer id="contact"><div class="wrap">
  <div><p class="say"><a href="mailto:{PROFILE['email']}">{PROFILE['email']}</a></p><a href="{PROFILE['linkedin']}">LinkedIn</a></div>
  <p>Charts on this site are drawn from my own pipelines&rsquo; data: U.S. EIA, SEC EDGAR and Open-Meteo.</p>
</div></footer>
<script>{JS}</script>
</body>
</html>
"""


# =============================================================================================== build
def build() -> Path:
    dom, capq, latest = load_series()
    pub = HERE / ("preview" if PREVIEW else "public")
    if pub.exists():
        shutil.rmtree(pub)
    (pub / "projects").mkdir(parents=True)
    (pub / "about").mkdir()
    (pub / "index.html").write_text(home(dom, capq, latest))
    (pub / "projects" / "index.html").write_text(projects_page(dom, capq, latest))
    (pub / "about" / "index.html").write_text(about_page())
    photos = [p for p in (HERE / "photos").glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    if photos:
        (pub / "photos").mkdir()
        for p in photos:
            shutil.copy2(p, pub / "photos" / p.name)
    (pub / "README.md").write_text("Personal site of Utkarsh Agarwal. These pages are built and published automatically.\n")
    if (HERE / "CNAME").exists():
        (pub / "CNAME").write_text((HERE / "CNAME").read_text().strip() + "\n")
    redirect = ('<!DOCTYPE html><meta charset="utf-8"><title>Moved</title><meta http-equiv="refresh" content="0; url={u}">'
                '<link rel="canonical" href="{u}"><a href="{u}">This page moved here.</a>')
    for old, new in [("follow-the-power", "/gigawatt-ledger/"), ("grid-pulse", "/sundown/")]:
        (pub / old).mkdir(parents=True, exist_ok=True)
        (pub / old / "index.html").write_text(redirect.format(u=new))
    return pub


if __name__ == "__main__":
    print(build())
