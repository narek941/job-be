import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from armapply.main import app

@pytest.fixture(autouse=True)
def mock_db_init():
    with patch("armapply.main.init_app_db"):
        yield

@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "armapply", "version": "2.0.0"}
