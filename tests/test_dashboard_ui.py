from fastapi.testclient import TestClient
from VoiceClonerAppServer.main import app

client = TestClient(app)

def test_dashboard_endpoint_serves_html():
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "VOICE CLONER" in res.text or "Voice Cloner" in res.text
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

def test_dashboard_html_contains_keeper_tabs():
    from pathlib import Path
    html_path = Path("VoiceClonerAppServer/static/dashboard/index.html")
    if not html_path.exists():
        html_path = Path("static/dashboard/index.html")
    html = html_path.read_text(encoding="utf-8")
    assert "VOICE CLONER" in html
    assert "tab-btn-overview" in html
    assert "tab-btn-analysis" in html
    assert "tab-btn-realtime" in html
    assert "tab-btn-events" in html
    assert "tab-btn-workers" in html
    assert "tab-btn-keys" in html
    assert "tab-btn-settings" in html
    assert "chartTokenUsageAnalysis" in html
    assert "chartAnalysisComposition" in html
    assert "maxPreviewCharLimitInput" in html
    assert "maxSpeechCharLimitInput" in html
    assert "CLIProxyAPI" not in html
    assert "tab-btn-ranking" not in html

def test_dashboard_js_implements_keeper_logic():
    from pathlib import Path
    js_path = Path("VoiceClonerAppServer/static/dashboard/app.js")
    if not js_path.exists():
        js_path = Path("static/dashboard/app.js")
    js = js_path.read_text(encoding="utf-8")
    assert "switchKTab" in js
    assert "initSparklines" in js
    assert "initHeatmaps" in js
    assert "chartTrendsMain" in js
    assert "initAnalysisTokenUsageChart" in js
    assert "initAnalysisCompositionChart" in js
    assert "initAnalysisLatencyChart" in js
    assert "saveSystemSettings" in js



