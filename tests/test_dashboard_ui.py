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
