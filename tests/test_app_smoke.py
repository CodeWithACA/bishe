from fastapi.testclient import TestClient

from app import app


def test_index_and_config_load_without_simulator():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "控制权" in response.text
        config = client.get("/api/config")
        assert config.status_code == 200
        assert "school" in {item["id"] for item in config.json()["capabilities"]["locations"]}
        mcp_response = client.post("/mcp", json={})
        assert mcp_response.status_code not in {404, 307, 308}
