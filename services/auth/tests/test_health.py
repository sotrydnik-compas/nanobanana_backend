from fastapi.testclient import TestClient


def test_health_ok():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/auth/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}