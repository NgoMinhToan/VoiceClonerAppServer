import asyncio
import uuid
import pytest
from fastapi.testclient import TestClient

from VoiceClonerAppServer.main import app
from VoiceClonerAppServer.config.settings_manager import settings_manager
from VoiceClonerAppServer.core.database import AsyncSessionLocal, init_db
from VoiceClonerAppServer.models.db_models import User

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())


def test_private_network_access_pna_options_header():
    """Verify server responds with Access-Control-Allow-Private-Network on preflight."""
    headers = {
        "Origin": "https://ngominhtoan.github.io",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Private-Network": "true",
    }
    res = client.options("/health", headers=headers)
    assert res.headers.get("access-control-allow-private-network") == "true" or res.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_private_network_access_get_header():
    """Verify server appends Access-Control-Allow-Private-Network on GET responses."""
    headers = {
        "Origin": "https://ngominhtoan.github.io",
    }
    res = client.get("/health", headers=headers)
    assert res.headers.get("access-control-allow-private-network") == "true" or res.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_user_management_api_flow():
    m_key = settings_manager.get_settings().security.management_key
    unique_id = f"usr_{uuid.uuid4().hex[:8]}"
    unique_email = f"tester_{uuid.uuid4().hex[:8]}@example.com"

    # 1. Seed test user directly into DB
    async def create_user():
        async with AsyncSessionLocal() as session:
            test_user = User(
                id=unique_id,
                email=unique_email,
                full_name="Nguyễn Văn Test",
                auth_provider="local",
                max_cloned_slots=3,
            )
            session.add(test_user)
            await session.commit()

    asyncio.run(create_user())

    # 2. Query users list via Management API
    res = client.get("/v1/management/users", headers={"X-Management-Key": m_key})
    assert res.status_code == 200
    users = res.json()
    assert any(u["id"] == unique_id for u in users)

    # 3. Update user max_cloned_slots
    patch_res = client.patch(
        f"/v1/management/users/{unique_id}",
        headers={"X-Management-Key": m_key},
        json={"max_cloned_slots": 5}
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["max_cloned_slots"] == 5

    # 4. Delete user
    del_res = client.delete(
        f"/v1/management/users/{unique_id}",
        headers={"X-Management-Key": m_key}
    )
    assert del_res.status_code == 200
