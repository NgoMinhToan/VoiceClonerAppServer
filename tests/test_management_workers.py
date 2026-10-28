import pytest
from httpx import AsyncClient, ASGITransport
from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.core.settings_manager import settings_manager

@pytest.mark.asyncio
async def test_worker_management_api_crud():
    transport = ASGITransport(app=app)
    headers = {"X-Management-Key": "vc_admin_secret_key_123"}
    test_worker_id = "test-worker-temp-1"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            # 0. Reject invalid worker URL scheme
            bad_res = await ac.post(
                "/v1/management/workers",
                headers=headers,
                json={
                    "id": "bad-worker",
                    "url": "ftp://127.0.0.1:8009",
                    "api_key": "",
                    "weight": 1,
                    "is_active": True,
                },
            )
            assert bad_res.status_code == 400

            # 1. Create worker
            res = await ac.post(
                "/v1/management/workers",
                headers=headers,
                json={
                    "id": test_worker_id,
                    "url": "http://127.0.0.1:8009/",
                    "api_key": "secret",
                    "weight": 2,
                    "is_active": True,
                },
            )
            assert res.status_code == 201

            # 2. Toggle worker
            res = await ac.patch(
                f"/v1/management/workers/{test_worker_id}/toggle?is_active=false",
                headers=headers,
            )
            assert res.status_code == 200
            assert res.json()["is_active"] is False

            # 3. Delete worker
            res = await ac.delete(
                f"/v1/management/workers/{test_worker_id}",
                headers=headers,
            )
            assert res.status_code == 200

            # Verify deleted
            res = await ac.delete(
                f"/v1/management/workers/{test_worker_id}",
                headers=headers,
            )
            assert res.status_code == 404
        finally:
            # Rollback guarantee: remove test worker if still present
            settings = settings_manager.get_settings()
            cleaned = [w.model_dump() for w in settings.model_workers if w.id != test_worker_id]
            if len(cleaned) != len(settings.model_workers):
                settings_manager.update_settings({"model_workers": cleaned})

@pytest.mark.asyncio
async def test_cache_purge_api():
    transport = ASGITransport(app=app)
    headers = {"X-Management-Key": "vc_admin_secret_key_123"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/v1/management/cache/purge", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "deleted_files" in data
