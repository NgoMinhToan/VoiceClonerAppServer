from fastapi.testclient import TestClient
from VoiceClonerAppServer.main import app

client = TestClient(app)

def test_dashboard_endpoint_serves_html():
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "Voice Cloner Management Console" in res.text
    assert "Chart.js" in res.text or "chart.js" in res.text.lower()
    assert "X-Management-Key" in res.text or "management" in res.text.lower()

def test_dashboard_static_assets_delivered():
    app_js = client.get("/static/dashboard/app.js")
    assert app_js.status_code == 200
    assert "renderMetricsCharts" in app_js.text or "fetchMetrics" in app_js.text

def test_dashboard_styles_contains_keeper_classes():
    from pathlib import Path
    css_path = Path("VoiceClonerAppServer/static/dashboard/styles.css")
    if not css_path.exists():
        css_path = Path("static/dashboard/styles.css")
    css = css_path.read_text(encoding="utf-8")
    assert ".k-card" in css
    assert ".k-glow-blue" in css
    assert ".heat-cell" in css

