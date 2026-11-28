"""End-to-end: pipeline orchestration with faked sources, then export + dashboard build on the result."""
import json

import pandas as pd
import pytest

from fixtures import load_synthetic, load_weather_zone
from ftp import config, dashboard, db, eia, export, filings, pipeline, sec, weather
from ftp.mcp_server import Server


def _facts(rev_scale=1.0):
    items = []
    for y in range(2019, 2026):
        for q, (a, b) in enumerate([("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30")], 1):
            items.append({"start": f"{y}-{a}", "end": f"{y}-{b}", "val": rev_scale * (100 + 10 * y - 20000 + q),
                          "filed": f"{y}-{int(b[:2]) + 1:02d}-15", "form": "10-Q", "accn": f"{y}{q}"})
        items.append({"start": f"{y}-01-01", "end": f"{y}-12-31", "val": rev_scale * (4 * (100 + 10 * y - 20000) + 10),
                      "filed": f"{y + 1}-02-15", "form": "10-K", "accn": f"{y}K"})
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": items}},
                                  "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": items}}}}}


def test_pipeline_fault_isolation_incremental_and_quality(monkeypatch):
    monkeypatch.setattr(config, "COMPANIES", {k: config.COMPANIES[k] for k in ("NVDA", "MSFT", "AMD")})
    monkeypatch.setattr(config, "GRID_ZONES", config.GRID_ZONES[:1])
    calls = {"zone": []}

    def facts(cik, session=None):
        if cik == config.COMPANIES["AMD"]["cik"]:
            raise RuntimeError("simulated SEC outage for one company")
        return _facts()

    def zone(parent, z, start, end, s=None):
        calls["zone"].append(start)
        hours = pd.date_range(start, f"{end} 23:00", freq="h", tz="UTC")
        return pd.DataFrame({"zone": z, "parent": parent, "period_utc": hours.strftime("%Y-%m-%d %H:%M:%S"), "demand_mwh": 10_000.0})

    monkeypatch.setattr(sec, "fetch_companyfacts", facts)
    monkeypatch.setattr(sec, "save_raw", lambda *a: None)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    monkeypatch.setattr(eia, "zone_hourly", zone)
    monkeypatch.setattr(eia, "state_sales", lambda *a, **k: pd.DataFrame(columns=["state", "sector", "month", "sales_gwh"]))
    monkeypatch.setattr(weather, "fetch_daily", lambda z, a, b, s=None: pd.DataFrame(columns=["zone", "date", "tmean_c", "tmin_c", "tmax_c"]))
    monkeypatch.setattr(filings, "load", lambda conn: 0)
    out = pipeline.run(start="2025-01-01")
    assert out["sec"] > 0 and out["zone_hourly"] > 0                 # AMD's failure didn't stop the others
    conn = db.connect()
    assert {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM fin_quarterly")} == {"NVDA", "MSFT"}
    q = pd.read_sql_query("SELECT * FROM quality_checks", conn)
    assert q[(q.subject == "AMD revenue") & (q.check_id == "has_data")]["passed"].iloc[0] == 0
    pipeline.run(start="2025-01-01")                                    # second run resumes near the end
    assert calls["zone"][0] == "2025-01-01" and calls["zone"][-1] > "2025-06-01"
    assert conn.execute("SELECT COUNT(*) FROM zone_hourly").fetchone()[0] == len(set(
        pd.read_sql_query("SELECT period_utc FROM zone_hourly", conn)["period_utc"]))      # idempotent: no duplicates


def test_upstream_outage_skips_the_source_but_keeps_the_run(monkeypatch):
    """An EIA timeout on a day when stored data is fresh is a warning, not a failed build."""
    import requests
    monkeypatch.setattr(config, "COMPANIES", {k: config.COMPANIES[k] for k in ("NVDA", "MSFT")})
    monkeypatch.setattr(config, "GRID_ZONES", [z for z in config.GRID_ZONES if z[1] == config.PRIMARY_ZONE] or config.GRID_ZONES[:1])
    state = {"down": False}

    def zone(parent, z, start, end, s=None):
        if state["down"]:
            raise requests.exceptions.ReadTimeout("simulated EIA timeout")
        hours = pd.date_range(start, f"{end} 23:00", freq="h", tz="UTC")
        return pd.DataFrame({"zone": z, "parent": parent, "period_utc": hours.strftime("%Y-%m-%d %H:%M:%S"), "demand_mwh": 10_000.0})

    monkeypatch.setattr(sec, "fetch_companyfacts", lambda cik, session=None: _facts())
    monkeypatch.setattr(sec, "save_raw", lambda *a: None)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    monkeypatch.setattr(eia, "zone_hourly", zone)
    monkeypatch.setattr(eia, "state_sales", lambda *a, **k: pd.DataFrame(columns=["state", "sector", "month", "sales_gwh"]))
    monkeypatch.setattr(weather, "fetch_daily", lambda z, a, b, s=None: pd.DataFrame(columns=["zone", "date", "tmean_c", "tmin_c", "tmax_c"]))
    monkeypatch.setattr(filings, "load", lambda conn: 0)
    pipeline.run(start="2025-01-01")
    state["down"] = True
    out = pipeline.run(start="2025-01-01")                              # no RuntimeError
    assert out["zone_hourly"] == 0
    conn = db.connect()
    assert conn.execute("SELECT status FROM pipeline_runs WHERE step='zone_hourly' ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "skipped"


def test_pipeline_critical_quality_failure_fails_the_run(monkeypatch):
    monkeypatch.setattr(config, "COMPANIES", {"MSFT": config.COMPANIES["MSFT"]})        # no NVDA -> critical failure
    monkeypatch.setattr(sec, "fetch_companyfacts", lambda cik, session=None: _facts())
    monkeypatch.setattr(sec, "save_raw", lambda *a: None)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    monkeypatch.setattr(eia, "state_sales", lambda *a, **k: pd.DataFrame(columns=["state", "sector", "month", "sales_gwh"]))
    monkeypatch.setattr(weather, "fetch_daily", lambda z, a, b, s=None: pd.DataFrame(columns=["zone", "date", "tmean_c", "tmin_c", "tmax_c"]))
    monkeypatch.setattr(filings, "load", lambda conn: 0)
    with pytest.raises(RuntimeError, match="quality"):
        pipeline.run(skip_hourly=True)


def test_export_and_dashboard_build_end_to_end(tmp_path, monkeypatch):
    conn = db.connect()
    load_synthetic(conn, True)
    load_weather_zone(conn, "AEP", growth=0.06)
    from ftp import quality
    quality.run_all(conn)
    conn.close()
    monkeypatch.setattr(export, "OUT", tmp_path / "data")
    files = export.build()
    assert files["fin_quarterly.csv"] > 0 and (tmp_path / "data" / "README.md").exists()
    for k, v in {"OUT": tmp_path / "index.html", "EVALS": tmp_path / "none.json", "MEMO": tmp_path / "none2.json",
                 "MANIFEST": tmp_path / "data" / "manifest.json"}.items():
        monkeypatch.setattr(dashboard, k, v)
    dashboard.build()
    html = (tmp_path / "index.html").read_text()
    payload = json.loads(html.split('<script id="data" type="application/json">')[1].split("</script>")[0].replace("<\\/", "</"))
    assert {"series", "zones", "chain", "exposure", "findings", "agent", "versions", "quality"} <= set(payload)
    assert "AEP" in payload["zones"] and payload["zones"]["AEP"]["regimes"]["segments"]
    assert payload["findings"] and "__DATA__" not in html


def test_mcp_server_in_process():
    conn = db.connect()
    load_synthetic(conn, True)
    conn.close()
    srv = Server(config.DB_PATH)
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    r = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "calculate", "arguments": {"expression": "2*21"}}})
    assert json.loads(r["result"]["content"][0]["text"])["result"] == 42
    assert srv.handle({"jsonrpc": "2.0", "id": 8, "method": "prompts/list"})["result"]["prompts"]
    tt = Server(config.DB_PATH, as_of="2021-01-01").handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    assert "sql" not in {t["name"] for t in tt["result"]["tools"]}     # time-travel mode hides raw SQL


def test_data_contract_and_history(tmp_path):
    bad = pd.DataFrame({"ticker": ["X"], "metric": ["revenue"]})
    assert any("missing column" in p for p in export.validate("fin_quarterly.csv", bad))
    good = pd.DataFrame({"state": ["VA"], "sector": ["COM"], "month": ["2025-01"], "sales_gwh": [1.0]})
    assert export.validate("state_sales.csv", good) == []
    p = tmp_path / "history.csv"
    export.append_history({"a": 1.0}, p)
    h = export.append_history({"a": 2.0, "b": None}, p)            # same day: replaced, None skipped
    assert h["value"].tolist() == [2.0]
