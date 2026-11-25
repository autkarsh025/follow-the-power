"""Build Utkarsh Agarwal's personal site (site/public/) from real project data.

The public site (autkarsh025.github.io) holds only built pages; this script stays private.
Figures are drawn at build time from the Gigawatt Ledger open dataset (docs/data); the
status readout is refreshed in the browser from each pipeline's published files.
Usage: python site/build_site.py
"""
from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "docs" / "data"
FALLBACK = json.loads((HERE / "figdata.json").read_text())
HYPERSCALERS = {"MSFT", "GOOGL", "AMZN", "META"}

# ----------------------------------------------------------------------------------------- content
# Everything below comes from the résumé. Phone number deliberately left off the public site.
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


# ----------------------------------------------------------------------------------------- data
def _rows(name):
    p = DATA / name
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def load_series():
    """DOM weather-adjusted monthly load, hyperscaler capex by quarter, and the latest headline values."""
    dom = [(r["month"], float(r["normalized_mw"])) for r in _rows("zone_weather_normalized.csv")
           if r["zone"] == "DOM" and r["normalized_mw"]]
    cap = defaultdict(dict)
    for r in _rows("fin_quarterly.csv"):
        if r["metric"] == "capex" and r["ticker"] in HYPERSCALERS:
            cap[r["quarter"]][r["ticker"]] = float(r["value_usd"])
    capq = sorted((q, sum(v.values()) / 1e9) for q, v in cap.items() if len(v) == len(HYPERSCALERS))
    latest = {}
    for r in _rows("history.csv"):
        latest[r["metric"]] = float(r["value"])
    if len(dom) < 24 or len(capq) < 8:  # no local dataset: use the snapshot
        dom = list(zip(FALLBACK["dom_m"], FALLBACK["dom"]))
        capq = list(zip(FALLBACK["cap_q"], FALLBACK["cap"]))
    capq = [x for x in capq if x[0] >= "2019Q1"]
    return dom, capq, latest


# ----------------------------------------------------------------------------------------- figures
def trace(dom, W=1000, H=150):
    """Hero: the load trace, drawn edge to edge."""
    vals = [v for _, v in dom]
    lo, hi = min(vals), max(vals)
    pad = 6
    pts = " ".join(f"{W * i / (len(vals) - 1):.1f},{pad + (H - 2 * pad) * (hi - v) / (hi - lo):.1f}" for i, v in enumerate(vals))
    years = []
    for i, (m, _) in enumerate(dom):
        if m.endswith("-01"):
            years.append(f'<text x="{W * i / (len(vals) - 1) + 4:.1f}" y="{H + 18}" class="tk">{m[:4]}</text>'
                         f'<line x1="{W * i / (len(vals) - 1):.1f}" x2="{W * i / (len(vals) - 1):.1f}" y1="{H + 4}" y2="{H + 8}" class="tl"/>')
    first, last = dom[0], dom[-1]
    return (f'<svg class="trace" viewBox="0 0 {W} {H + 24}" preserveAspectRatio="none" role="img" '
            f'aria-label="Northern Virginia weather-adjusted electricity load, {first[1]:,.0f} MW in {first[0][:4]} to {last[1]:,.0f} MW in {last[0]}">'
            f'<line x1="0" x2="{W}" y1="{H + 4}" y2="{H + 4}" class="tl"/>{"".join(years)}'
            f'<polyline points="{pts}" class="line" pathLength="1"/></svg>')


def bars(capq, W=320, H=90):
    vals = [v for _, v in capq]
    mx = max(vals)
    bw = W / len(vals)
    out = [f'<rect x="{i * bw + 0.75:.2f}" y="{H - H * v / mx:.1f}" width="{bw - 1.5:.2f}" height="{H * v / mx:.1f}" '
           f'class="{"hi" if i == len(vals) - 1 else "b"}"><title>{q[:4]} Q{q[-1]}: ${v:.1f}B</title></rect>'
           for i, (q, v) in enumerate(capq)]
    return f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="Big tech capital spending per quarter, rising to ${vals[-1]:.0f} billion">{"".join(out)}</svg>'


def duck(W=320, H=90):
    d = FALLBACK["duck"]
    lo, hi = min(min(d["battery"]), 0), max(d["battery"])
    y = lambda v: H * (hi - v) / (hi - lo)
    bw = W / 24
    out = []
    for i, v in enumerate(d["battery"]):
        y0, y1 = sorted([y(0), y(v)])
        out.append(f'<rect x="{i * bw + 1:.1f}" y="{y0:.1f}" width="{bw - 2:.1f}" height="{max(y1 - y0, .8):.1f}" '
                   f'class="{"hi" if v > 0 else "b"}"><title>{i}:00, {v:+.1f} GW</title></rect>')
    out.append(f'<line x1="0" x2="{W}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="tl"/>')
    return f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="California batteries charge at midday and discharge after sunset">{"".join(out)}</svg>'


# ----------------------------------------------------------------------------------------- page
def rows(items):
    return "".join(
        f'<li class="row"><span class="when">{html.escape(w)}</span><div><h3>{html.escape(t)}</h3>'
        f'<p class="org">{html.escape(o)}</p><p>{html.escape(d)}</p></div></li>' for w, t, o, d in items)


def page(dom, capq, latest):
    cap_now = latest.get("hyperscaler_capex_b", capq[-1][1])
    cap_yoy = latest.get("hyperscaler_capex_yoy")
    base = [v for m, v in dom if m.startswith(dom[0][0][:4])]
    dom_add = sum(v for _, v in dom[-12:]) / 12 - sum(base) / len(base)  # last 12 months vs first year, as on the dashboard
    grid_hours = latest.get("grid_hours")
    sec_points = latest.get("sec_data_points")
    q = capq[-1][0]
    projects = f"""
    <article class="project">
      <div class="ptext">
        <h3><a href="/gigawatt-ledger/">Gigawatt Ledger</a></h3>
        <p class="sub">Tracks AI spending from big tech to the power grid</p>
        <p>A daily pipeline joins SEC filings from 26 companies across the AI supply chain with hourly electricity demand
        in five data-center regions, weather removed. Every number keeps the date it became public, so any past day can be
        replayed without hindsight. An AI analyst answers questions from 4,600+ filing passages, and code checks every
        figure it writes; it passes all 22 cases in its eval suite, prompt injection included.</p>
        <dl class="nums">
          <div><dt>Big tech capex, {q[:4]} Q{q[-1]}</dt><dd>${cap_now:.1f}B{f'<small> up {cap_yoy:.0f}% in a year</small>' if cap_yoy else ''}</dd></div>
          <div><dt>Always-on load added in N. Virginia since 2019</dt><dd>+{dom_add:,.0f} MW</dd></div>
        </dl>
        <p class="stack">Python, SQL, Claude API, RAG, MCP, evals, GitHub Actions</p>
        <p class="links"><a href="/gigawatt-ledger/">Open the dashboard</a><a href="/gigawatt-ledger/data/README.md">Open dataset</a></p>
      </div>
      <figure>{bars(capq)}<figcaption>Big tech capex per quarter, 2019 to now</figcaption></figure>
    </article>

    <article class="project">
      <div class="ptext">
        <h3><a href="/sundown/">Sundown</a></h3>
        <p class="sub">How batteries carry U.S. grids through the evening peak</p>
        <p>Every morning a pipeline pulls hourly data for the seven major U.S. grids into SQL with Airflow and Spark,
        checks it, and rebuilds a live dashboard. Batteries now cover about a quarter of California's evening peak.
        The quality checks and 59 tests caught 3 errors in the government's own source data, and the carbon analysis shows
        midday EV charging in California emits about 60% less CO₂ than overnight.</p>
        <dl class="nums">
          <div><dt>California evening peak from batteries</dt><dd data-live="sd-share">25.4%</dd></div>
          <div><dt>Hourly records validated</dt><dd data-live="sd-rows">587,104</dd></div>
        </dl>
        <p class="stack">Python, SQL, Airflow, Spark, pytest, Plotly</p>
        <p class="links"><a href="/sundown/">Open the dashboard</a></p>
      </div>
      <figure>{duck()}<figcaption>California batteries on an average day: charging at noon, discharging after sunset</figcaption></figure>
    </article>

    <article class="project">
      <div class="ptext">
        <h3>BMW engine and electronics tuning</h3>
        <p class="sub">Personal project, May – Aug 2026</p>
        <p>Reprogrammed the engine control unit in C and Python, tuning 125 calibration parameters across ignition timing,
        fuel delivery, boost and throttle response for a 17% gain in engine performance. Reconfigured 6 electronic control
        modules to add real-time G-force telemetry and radar displays to the car's own screens.</p>
        <p class="stack">C, Python, ECU calibration</p>
      </div>
    </article>"""

    readout = f"""
    <table class="status" aria-label="Pipeline status">
      <tr><th scope="row"><a href="/gigawatt-ledger/">gigawatt-ledger</a></th><td><i class="dot" data-live="gl-dot"></i><span data-live="gl-when">daily</span></td>
          <td data-live="gl-what">{f"{grid_hours:,.0f} grid hours, {sec_points:,.0f} SEC data points" if grid_hours and sec_points else "SEC filings and hourly grid data"}</td></tr>
      <tr><th scope="row"><a href="/sundown/">sundown</a></th><td><i class="dot" data-live="sd-dot"></i><span data-live="sd-when">daily</span></td>
          <td data-live="sd-what">hourly data for 7 U.S. grids</td></tr>
    </table>"""

    return TEMPLATE.safe_substitute(
        name=PROFILE["name"], email=PROFILE["email"], linkedin=PROFILE["linkedin"],
        trace=trace(dom), dom_first=f"{dom[0][1]:,.0f}", dom_last=f"{dom[-1][1]:,.0f}", dom_year=dom[0][0][:4],
        readout=readout, projects=projects, experience=rows(EXPERIENCE), leadership=rows(LEADERSHIP),
        education=rows(EDUCATION),
        skills="".join(f"<div><dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd></div>" for k, v in SKILLS),
    )


from string import Template  # noqa: E402


class _T(Template):
    delimiter = "@"


TEMPLATE = _T(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@name</title>
<meta name="description" content="Data science and applied math at UC Berkeley. Data pipelines and AI tools on public energy and financial data.">
<meta property="og:title" content="@name">
<meta property="og:description" content="Data pipelines and AI tools on public energy and financial data.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23171a1f'/%3E%3Cpolyline points='5,22 11,19 15,21 20,13 27,8' fill='none' stroke='%23ffffff' stroke-width='2.6' stroke-linejoin='round' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400..800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script>try{const t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{
  --bg:#ffffff; --ink:#171a1f; --sec:#565c65; --mute:#868c94; --rule:#e5e7ea; --soft:#f4f5f7;
  --accent:#2a4fd6; --bar:#c9d3f4; --ok:#1f8a4c;
  --sans:"Schibsted Grotesk",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,monospace;
}
:root[data-theme=dark]{--bg:#0f1115;--ink:#e8eaee;--sec:#a6acb5;--mute:#757b85;--rule:#262a31;--soft:#171a20;--accent:#8aa2ff;--bar:#2e3b66;--ok:#4cc27e}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--ink:#e8eaee;--sec:#a6acb5;--mute:#757b85;--rule:#262a31;--soft:#171a20;--accent:#8aa2ff;--bar:#2e3b66;--ok:#4cc27e}}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:72px}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16.5px/1.62 var(--sans);-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration:underline;text-decoration-color:var(--rule);text-decoration-thickness:1.5px;text-underline-offset:3px;transition:text-decoration-color .15s}
a:hover{text-decoration-color:var(--accent)}
a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:2px}
.wrap{max-width:960px;margin:0 auto;padding:0 32px}

header.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid transparent;transition:border-color .2s}
header.top.scrolled{border-bottom-color:var(--rule)}
.top .wrap{display:flex;align-items:center;justify-content:space-between;height:60px}
.top .me{font-weight:650;text-decoration:none;letter-spacing:-.01em}
.top nav{display:flex;gap:24px;align-items:center;font-size:15px}
.top nav a{text-decoration:none;color:var(--sec)}.top nav a:hover{color:var(--ink)}
#theme{font:inherit;font-size:14px;color:var(--sec);background:none;border:1px solid var(--rule);border-radius:6px;padding:3px 10px;cursor:pointer}
#theme:hover{color:var(--ink);border-color:var(--mute)}

.hero{padding:88px 0 0}
.hero h1{font-size:clamp(44px,7.4vw,76px);line-height:.98;letter-spacing:-.045em;font-weight:720;margin:0}
.hero .lede{font-size:21px;line-height:1.5;color:var(--sec);max-width:31em;margin:22px 0 0;letter-spacing:-.005em}
.hero .lede b{color:var(--ink);font-weight:550}
.contact{display:flex;gap:22px;margin-top:22px;font-size:15.5px}
.trace{display:block;width:100%;height:170px;margin-top:64px;overflow:visible}
.trace .line{fill:none;stroke:var(--accent);stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.trace .tl{stroke:var(--rule);stroke-width:1;vector-effect:non-scaling-stroke}
.trace .tk{fill:var(--mute);font:12px var(--sans)}
@media (prefers-reduced-motion:no-preference){.trace .line{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 2.2s cubic-bezier(.4,0,.2,1) .25s forwards}}
@keyframes draw{to{stroke-dashoffset:0}}
.tcap{display:flex;justify-content:space-between;gap:24px;font-size:14px;color:var(--sec);margin-top:10px}
.tcap b{color:var(--ink);font-weight:600}

.status{width:100%;border-collapse:collapse;margin-top:36px;font:14px/1.4 var(--mono);color:var(--sec);border-top:1px solid var(--rule)}
.status th,.status td{text-align:left;padding:11px 18px 11px 0;border-bottom:1px solid var(--rule);font-weight:400;white-space:nowrap}
.status th{color:var(--ink);font-weight:500;width:190px}
.status th a{text-decoration:none}.status th a:hover{text-decoration:underline}
.status td:last-child{white-space:normal;width:100%}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--mute);margin-right:9px;vertical-align:1px}
.dot.ok{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 22%,transparent)}

section{display:grid;grid-template-columns:190px 1fr;gap:0 32px;margin-top:104px}
section>h2{font-size:15px;font-weight:600;color:var(--sec);margin:4px 0 0;letter-spacing:0;position:sticky;top:84px;align-self:start}
.about p{font-size:19px;line-height:1.6;margin:0 0 16px;max-width:34em;letter-spacing:-.005em}
.about p:last-child{margin:0}

.project{display:flex;flex-direction:column;padding:0 0 52px;margin-bottom:52px;border-bottom:1px solid var(--rule)}
.project:last-child{border-bottom:0;margin-bottom:0;padding-bottom:0}
.project h3{font-size:26px;line-height:1.2;letter-spacing:-.025em;font-weight:680;margin:0}
.project h3 a{text-decoration:none}.project h3 a:hover{text-decoration:underline;text-decoration-color:var(--accent)}
.project .sub{color:var(--sec);margin:4px 0 14px}
.project p{margin:0 0 14px;max-width:36em}
.nums{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:22px 0;max-width:36em}
.nums div{border-top:2px solid var(--ink);padding-top:10px}
.nums dt{font-size:13.5px;color:var(--sec);line-height:1.35}
.nums dd{margin:4px 0 0;white-space:nowrap;font-size:30px;font-weight:680;letter-spacing:-.03em;line-height:1.1}
.nums small{display:block;font-size:13.5px;font-weight:450;color:var(--sec);letter-spacing:0;margin-top:3px}
.stack{font-size:14.5px;color:var(--mute)}
.links{display:flex;gap:22px;font-weight:550;font-size:15.5px;margin-top:6px!important}
figure{order:-1;margin:0 0 26px;padding:18px 0 0}
figure svg{width:100%;height:84px;display:block;overflow:visible}
figure .b{fill:var(--bar)}figure .hi{fill:var(--accent)}figure .tl{stroke:var(--mute);stroke-width:.6}
figcaption{font-size:13px;color:var(--mute);margin-top:10px;line-height:1.4}

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

footer{margin:120px 0 0;padding:28px 0 48px;border-top:1px solid var(--rule)}
footer .wrap{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;font-size:14.5px;color:var(--sec)}
footer .say{font-size:28px;letter-spacing:-.03em;font-weight:680;color:var(--ink);margin:0 0 10px}
footer .say a{text-decoration-color:var(--accent)}

@media (max-width:820px){
  .wrap{padding:0 22px}
  .top nav .opt{display:none}
  .hero{padding-top:56px}.hero .lede{font-size:18.5px}
  .trace{height:120px;margin-top:44px}.trace .tk{display:none}
  section{grid-template-columns:1fr;margin-top:76px}
  section>h2{position:static;margin-bottom:18px}
  figure svg{height:64px}
  .row{grid-template-columns:1fr;gap:2px}.row .when{order:0;text-align:left}
  .skills div{grid-template-columns:1fr;gap:2px}
  .status th{width:auto}.status td:last-child{display:none}
  .tcap{flex-direction:column;gap:2px}
}
@media print{header.top,#theme{display:none}.trace .line{animation:none;stroke-dasharray:none}}
</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="me" href="#">@name</a>
  <nav><a class="opt" href="#work">Projects</a><a class="opt" href="#experience">Experience</a><a href="#contact">Contact</a><button id="theme" type="button">Dark</button></nav>
</div></header>

<main class="wrap">
  <div class="hero">
    <h1>@name</h1>
    <p class="lede">I study <b>data science and applied math at UC Berkeley</b> and build data pipelines and AI tools on public energy and financial data. Two of them are running right now.</p>
    <p class="contact"><a href="mailto:@email">@email</a><a href="@linkedin">LinkedIn</a></p>
    @trace
    <div class="tcap"><span>Northern Virginia's always-on electricity load, weather removed, monthly since @dom_year</span><span><b>@dom_first MW</b> to <b>@dom_last MW</b></span></div>
    @readout
  </div>

  <section class="about"><h2>About</h2><div>
    <p>I like problems where the data is public but nobody has lined it up yet. My two main projects follow money and electricity: one traces AI spending from big tech's filings down to the power grid, the other measures how much batteries carry U.S. grids after sunset.</p>
    <p>I transferred to Berkeley from UW–Madison, where I ran Division I cross country and track and did research on campus energy use. Outside of code I work on cars; I reprogrammed my BMW's engine computer myself.</p>
  </div></section>

  <section id="work"><h2>Projects</h2><div>@projects</div></section>
  <section id="experience"><h2>Experience</h2><ul class="list">@experience</ul></section>
  <section><h2>Leadership and athletics</h2><ul class="list">@leadership</ul></section>
  <section><h2>Education</h2><ul class="list">@education</ul></section>
  <section><h2>Skills</h2><dl class="skills">@skills</dl></section>
</main>

<footer id="contact"><div class="wrap">
  <div><p class="say"><a href="mailto:@email">@email</a></p><a href="@linkedin">LinkedIn</a></div>
  <p>Charts on this page are drawn from my own pipelines' data: U.S. EIA, SEC EDGAR and Open-Meteo.</p>
</div></footer>

<script>
(() => {
  const root = document.documentElement, btn = document.getElementById('theme');
  const dark = () => root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const label = () => { btn.textContent = dark() ? 'Light' : 'Dark'; };
  btn.onclick = () => { root.dataset.theme = dark() ? 'light' : 'dark'; try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {} label(); };
  label();
  const top = document.querySelector('header.top');
  addEventListener('scroll', () => top.classList.toggle('scrolled', scrollY > 8), { passive: true });

  const $$ = k => document.querySelector(`[data-live="${k}"]`);
  const ago = s => {
    const t = Date.parse(s.replace(' UTC', 'Z').replace(' ', 'T'));
    if (!t) return null;
    const h = (Date.now() - t) / 36e5;
    return { h, text: h < 1 ? 'updated under an hour ago' : h < 48 ? `updated ${Math.round(h)} hours ago` : `updated ${Math.round(h / 24)} days ago` };
  };
  const mark = (who, when) => { const a = when && ago(when); if (!a) return; $$(who + '-when').textContent = a.text; $$(who + '-dot').classList.toggle('ok', a.h < 36); };
  const n = x => Number(x).toLocaleString('en-US', { maximumFractionDigits: 0 });

  fetch('/gigawatt-ledger/data/manifest.json', { cache: 'no-store' }).then(r => r.ok && r.json()).then(m => m && mark('gl', m.built_at)).catch(() => {});
  fetch('/gigawatt-ledger/data/history.csv', { cache: 'no-store' }).then(r => r.ok && r.text()).then(t => {
    if (!t) return; const v = {};
    t.trim().split('\n').slice(1).forEach(l => { const [, k, x] = l.split(','); v[k] = x; });
    if (v.grid_hours && v.sec_data_points) $$('gl-what').textContent = `${n(v.grid_hours)} grid hours, ${n(v.sec_data_points)} SEC data points`;
  }).catch(() => {});
  fetch('/sundown/status.json', { cache: 'no-store' }).then(r => r.ok && r.json()).then(s => {
    if (!s) return; mark('sd', s.built_at);
    if (s.rows) { $$('sd-what').textContent = `${n(s.rows)} hourly records, ${s.grids || 7} grids`; $$('sd-rows').textContent = n(s.rows); }
    if (s.ciso_battery_share_30d != null) $$('sd-share').textContent = s.ciso_battery_share_30d + '%';
  }).catch(() => {});
})();
</script>
</body>
</html>
""")


def build() -> Path:
    dom, capq, latest = load_series()
    pub = HERE / "public"  # everything here is copied to the root of the public site
    pub.mkdir(exist_ok=True)
    out = pub / "index.html"
    out.write_text(page(dom, capq, latest))
    (pub / "README.md").write_text("Personal site of Utkarsh Agarwal. These pages are built and published automatically.\n")
    if (HERE / "CNAME").exists():
        (pub / "CNAME").write_text((HERE / "CNAME").read_text().strip() + "\n")
    redirect = ('<!DOCTYPE html><meta charset="utf-8"><title>Moved</title><meta http-equiv="refresh" content="0; url={u}">'
                '<link rel="canonical" href="{u}"><a href="{u}">This page moved here.</a>')
    for old, new in [("follow-the-power", "/gigawatt-ledger/"), ("grid-pulse", "/sundown/")]:
        (pub / old).mkdir(parents=True, exist_ok=True)
        (pub / old / "index.html").write_text(redirect.format(u=new))
    return out


if __name__ == "__main__":
    print(build())
