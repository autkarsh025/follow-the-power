"""Build the public landing page (site/index.html) with two small figures drawn from real project data.

The public site (autkarsh025.github.io) holds only built pages; this script and the dashboards' source stay private.
Usage: python site/build_site.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "figdata.json").read_text())
INK, SEC, RULE = "#16181b", "#5c6166", "#e3e5e8"
GREEN, AMBER, SOLAR, BATT = "#2c6e4f", "#9a5a08", "#e2a33b", "#1f6f6a"


def ledger_fig(W=760, H=230):
    """Left: hyperscaler capex per quarter (bars). Right: N. Virginia weather-adjusted always-on load (line)."""
    pad, gap = 34, 40
    pw = (W - gap) / 2
    cap, qs = D["cap"], D["cap_q"]
    top, bot = 46, H - 30
    mx = max(cap) * 1.05
    bw = pw / len(cap)
    out = [f'<text x="0" y="16" class="fl">Big tech capex, $B per quarter</text>']
    for i, v in enumerate(cap):
        h = (bot - top) * v / mx
        out.append(f'<rect x="{i * bw + 1:.1f}" y="{bot - h:.1f}" width="{bw - 2:.1f}" height="{h:.1f}" fill="{GREEN}" opacity="{0.35 + 0.65 * i / len(cap):.2f}"/>')
    out.append(f'<text x="{pw}" y="{bot - (bot - top) * cap[-1] / mx - 8:.1f}" text-anchor="end" class="fv">${cap[-1]:.1f}B</text>')
    for i, q in enumerate(qs):
        if q.endswith("Q1") and int(q[:4]) % 2 == 1:
            out.append(f'<text x="{i * bw:.1f}" y="{H - 10}" class="ft">\'{q[2:4]}</text>')
    out.append(f'<line x1="0" x2="{pw}" y1="{bot}" y2="{bot}" stroke="{RULE}"/>')
    # right panel
    x0 = pw + gap
    dom, ms = D["dom"], D["dom_m"]
    lo, hi = min(dom) * 0.97, max(dom) * 1.02
    pts = " ".join(f"{x0 + pw * i / (len(dom) - 1):.1f},{bot - (bot - top) * (v - lo) / (hi - lo):.1f}" for i, v in enumerate(dom))
    out.append(f'<text x="{x0}" y="16" class="fl">Northern Virginia always-on load, MW (weather-adjusted)</text>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="{GREEN}" stroke-width="2"/>')
    out.append(f'<text x="{x0 + pw}" y="{top - 6}" text-anchor="end" class="fv">{dom[-1]:,} MW</text>')
    out.append(f'<text x="{x0}" y="34" class="fs">up from {dom[0]:,} MW in 2019</text>')
    for i, m in enumerate(ms):
        if m.endswith("-01") and int(m[:4]) % 2 == 1:
            out.append(f'<text x="{x0 + pw * i / (len(dom) - 1):.1f}" y="{H - 10}" class="ft">\'{m[2:4]}</text>')
    out.append(f'<line x1="{x0}" x2="{x0 + pw}" y1="{bot}" y2="{bot}" stroke="{RULE}"/>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Big tech capital spending rising to ${cap[-1]:.0f} billion a quarter, and Northern Virginia always-on electricity load rising from {dom[0]:,} to {dom[-1]:,} megawatts">' + "".join(out) + "</svg>"


def sundown_fig(W=760, H=240):
    """California, average day: solar (area), batteries (bars, below zero = charging), demand (line). GW."""
    d = D["duck"]
    top, bot = 30, H - 30
    lo, hi = min(min(d["battery"]), 0) - 1, max(d["demand"]) * 1.05
    y = lambda v: top + (bot - top) * (hi - v) / (hi - lo)
    xw = W / 24
    out = [f'<text x="0" y="16" class="fl">California, an average day (GW)</text>']
    sol = " ".join(f"{(i + .5) * xw:.1f},{y(max(v, 0)):.1f}" for i, v in enumerate(d["solar"]))
    out.append(f'<polygon points="{.5 * xw:.1f},{y(0):.1f} {sol} {23.5 * xw:.1f},{y(0):.1f}" fill="{SOLAR}" opacity="0.35"/>')
    for i, v in enumerate(d["battery"]):
        y0, y1 = sorted([y(0), y(v)])
        out.append(f'<rect x="{i * xw + 4:.1f}" y="{y0:.1f}" width="{xw - 8:.1f}" height="{y1 - y0:.1f}" fill="{BATT}"/>')
    dem = " ".join(f"{(i + .5) * xw:.1f},{y(v):.1f}" for i, v in enumerate(d["demand"]))
    out.append(f'<polyline points="{dem}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    out.append(f'<line x1="0" x2="{W}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="{SEC}" stroke-width=".6"/>')
    for h, lab in [(0, "midnight"), (6, "6 am"), (12, "noon"), (18, "6 pm"), (23, "11 pm")]:
        out.append(f'<text x="{(h + .5) * xw:.1f}" y="{H - 8}" text-anchor="middle" class="ft">{lab}</text>')
    pk = max(range(24), key=lambda i: d["battery"][i])
    out.append(f'<text x="{(pk + .5) * xw:.1f}" y="{y(d["battery"][pk]) - 6:.1f}" text-anchor="middle" class="fs">batteries discharge after sunset</text>')
    mn = min(range(24), key=lambda i: d["battery"][i])
    out.append(f'<text x="{(mn + 3.2) * xw:.1f}" y="{y(d["battery"][mn]) + 4:.1f}" class="fs">batteries charge on midday solar</text>')
    out.append(f'<text x="{W}" y="{y(d["demand"][20]) - 8:.1f}" text-anchor="end" class="fs">demand</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="California average day: batteries charge at midday on solar and discharge after sunset">' + "".join(out) + "</svg>"


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Utkarsh Agarwal</title>
<meta name="description" content="Data and AI engineering projects on public energy and financial data.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..100,400..700&display=swap" rel="stylesheet">
<style>
:root{--ink:#16181b;--sec:#5c6166;--mute:#8a9096;--rule:#e3e5e8;--bg:#ffffff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Archivo,system-ui,sans-serif;font-size:17px;line-height:1.6;-webkit-font-smoothing:antialiased}
main{max-width:780px;margin:0 auto;padding:88px 28px 72px}
h1{font-size:60px;line-height:1;letter-spacing:-0.035em;font-weight:650;font-stretch:80%;margin:0}
.intro{font-size:20px;line-height:1.5;color:var(--sec);margin:18px 0 0;max-width:34em}
.contact{margin-top:18px;font-size:15.5px;color:var(--sec)}
.contact a{margin-right:18px}
a{color:var(--ink);text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{text-decoration-thickness:2px}
a:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
section{margin-top:84px}
section h2{font-size:32px;letter-spacing:-0.02em;font-weight:650;font-stretch:85%;margin:22px 0 6px}
section h2 a{text-decoration:none}section h2 a:hover{text-decoration:underline}
.what{font-size:15.5px;color:var(--mute);margin:0}
section p{margin:10px 0 0;max-width:38em}
.facts{list-style:none;padding:0;margin:18px 0 0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;border-top:1px solid var(--rule);padding-top:14px}
.facts b{display:block;font-size:24px;font-weight:650;font-stretch:80%;letter-spacing:-0.02em;font-variant-numeric:tabular-nums}
.facts span{font-size:14px;color:var(--sec);line-height:1.4;display:block}
.links{margin-top:20px;font-size:16px}.links a{margin-right:22px;font-weight:550}
svg{width:100%;height:auto;display:block;overflow:visible}
svg .fl{font:600 13px Archivo,sans-serif;fill:var(--ink)}
svg .fv{font:650 14px Archivo,sans-serif;fill:var(--ink)}
svg .fs{font:400 12px Archivo,sans-serif;fill:var(--sec)}
svg .ft{font:400 11.5px Archivo,sans-serif;fill:var(--mute)}
footer{margin-top:96px;padding-top:18px;border-top:1px solid var(--rule);font-size:14px;color:var(--mute)}
@media(max-width:640px){main{padding:56px 20px 56px}h1{font-size:44px}.intro{font-size:18px}.facts{grid-template-columns:1fr}}
</style>
</head>
<body>
<main>
  <h1>Utkarsh Agarwal</h1>
  <p class="intro">I build data pipelines and analysis tools on public energy and financial data. Both projects below run on their own every day and publish what they find.</p>
  __CONTACT__

  <section>
    __LEDGER_FIG__
    <h2><a href="/gigawatt-ledger/">Gigawatt Ledger</a></h2>
    <p class="what">AI infrastructure, from capital spending to the power grid</p>
    <p>Follows the AI build-out through 26 companies' SEC filings (cloud buyers, chips, servers, power equipment, data-center landlords and utilities) and through hourly electricity demand in five data-center regions, with weather removed. Every number is stored with the date it became public, so history can be replayed without hindsight. An AI analyst answers questions about the data, and code checks every figure it writes.</p>
    <ul class="facts">
      <li><b>26</b><span>companies tracked through SEC filings</span></li>
      <li><b>+4,900 MW</b><span>always-on load added in Northern Virginia since 2019</span></li>
      <li><b>22 of 22</b><span>evaluation cases passed by the AI analyst, three runs each</span></li>
    </ul>
    <p class="links"><a href="/gigawatt-ledger/">Open the dashboard</a><a href="/gigawatt-ledger/data/README.md">Open dataset</a></p>
  </section>

  <section>
    __SUNDOWN_FIG__
    <h2><a href="/sundown/">Sundown</a></h2>
    <p class="what">How batteries cover the evening peak on U.S. power grids</p>
    <p>An automated pipeline that collects hourly grid data for the seven major U.S. power grids every morning, checks it, stores it and measures how much grid-scale batteries help after sunset, when solar disappears and demand peaks. It also tracks how clean the grid is hour by hour, when an electric car is cleanest to charge, and which readings look abnormal.</p>
    <ul class="facts">
      <li><b>25.7%</b><span>of California's evening peak supplied by batteries, last 30 days</span></li>
      <li><b>7</b><span>U.S. power grids, hourly, updated every morning</span></li>
      <li><b>586,000</b><span>hourly records validated and stored</span></li>
    </ul>
    <p class="links"><a href="/sundown/">Open the dashboard</a></p>
  </section>

  <footer>Data from the U.S. Energy Information Administration, SEC EDGAR and Open-Meteo.</footer>
</main>
</body>
</html>
"""


def build(contact: dict | None = None) -> Path:
    contact = contact or json.loads((HERE / "contact.json").read_text()) if (HERE / "contact.json").exists() else (contact or {})
    links = [f'<a href="{v}">{k}</a>' for k, v in contact.items() if v]
    html = (PAGE.replace("__CONTACT__", f'<p class="contact">{"".join(links)}</p>' if links else "")
                .replace("__LEDGER_FIG__", ledger_fig()).replace("__SUNDOWN_FIG__", sundown_fig()))
    pub = HERE / "public"                     # everything here is copied to the root of the public site
    pub.mkdir(exist_ok=True)
    out = pub / "index.html"
    out.write_text(html)
    (pub / "README.md").write_text("Personal site of Utkarsh Agarwal. These pages are built and published automatically.\n")
    if (HERE / "CNAME").exists():
        (pub / "CNAME").write_text((HERE / "CNAME").read_text().strip() + "\n")
    redirect = '<!DOCTYPE html><meta charset="utf-8"><title>Moved</title><meta http-equiv="refresh" content="0; url={u}"><link rel="canonical" href="{u}"><a href="{u}">This page moved here.</a>'
    for old, new in [("follow-the-power", "/gigawatt-ledger/"), ("grid-pulse", "/sundown/")]:
        (pub / old).mkdir(parents=True, exist_ok=True)
        (pub / old / "index.html").write_text(redirect.format(u=new))
    return out


if __name__ == "__main__":
    print(build())
