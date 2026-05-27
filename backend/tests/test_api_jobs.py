from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_create_text_job_to_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "sync_conversions", True)
    monkeypatch.setattr(settings, "ai_api_key", "")

    with TestClient(app) as client:
        response = client.post("/api/jobs", data={"text": "Hello MarkdownEverything\n\nThis is a note."})
        assert response.status_code == 202
        payload = response.json()
        job_id = payload["job"]["id"]
        guest_token = payload["guest_token"]

        job_response = client.get(f"/api/jobs/{job_id}", params={"guest_token": guest_token})
        assert job_response.status_code == 200
        assert job_response.json()["status"] == "succeeded"

        markdown_response = client.get(f"/api/jobs/{job_id}/markdown", params={"guest_token": guest_token})
        assert markdown_response.status_code == 200
        assert "Hello MarkdownEverything" in markdown_response.json()["markdown"]

        asset = tmp_path / "jobs" / job_id / "assets" / "image.png"
        asset.write_bytes(b"fake-image")
        asset_response = client.get(f"/api/jobs/{job_id}/assets/image.png", params={"guest_token": guest_token})
        assert asset_response.status_code == 200
        assert asset_response.content == b"fake-image"

        download_response = client.get(f"/api/jobs/{job_id}/download", params={"guest_token": guest_token, "format": "zip"})
        assert download_response.status_code == 200
        assert download_response.headers["content-type"] == "application/zip"
