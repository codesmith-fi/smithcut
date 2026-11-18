"""FastAPI TestClient -testit web-rajapinnalle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from controller.editor import EditorController
from web.api import create_app

PROBE_META = {
    "duration": 10.0,
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "has_audio": True,
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def client(workspace: Path):
    editor = EditorController(workspace=workspace)
    application = create_app(workspace=workspace, editor=editor)

    def fake_render(timeline, output_path=None, mode="preview", workspace=None):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
        return str(path)

    with (
        patch("controller.editor.probe", return_value=PROBE_META),
        patch("controller.editor.ffmpeg_render", side_effect=fake_render),
    ):
        with TestClient(application) as test_client:
            test_client.editor = editor  # type: ignore[attr-defined]
            yield test_client


def _upload_video(client: TestClient, name: str = "clip.mp4") -> dict:
    files = {"file": (name, b"fake-video-bytes", "video/mp4")}
    data = {"kind": "auto"}
    res = client.post("/api/upload", files=files, data=data)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    return body


def test_get_state_empty(client: TestClient):
    res = client.get("/api/state")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["state"]["clip_count"] == 0
    assert body["state"]["lang"] == "fi"
    assert "tyhjä" in body["state"]["description"].lower()
    assert body["state"]["can_undo"] is False
    assert body["state"]["can_redo"] is False


def test_get_state_lang_en(client: TestClient):
    res = client.get("/api/state?lang=en")
    assert res.status_code == 200
    body = res.json()
    assert body["state"]["lang"] == "en"
    assert "empty" in body["state"]["description"].lower()

    _upload_video(client)
    res = client.get("/api/state?lang=en")
    state = res.json()["state"]
    assert state["clips"][0]["title"] == "Clip 1"
    assert "clip" in state["description"].lower()


def test_upload_video_imports_and_adds_clip(client: TestClient):
    body = _upload_video(client)
    assert body["kind"] == "video"
    assert body["state"]["clip_count"] == 1
    assert body["state"]["clips"][0]["index"] == 1
    assert len(body["state"]["sources"]) == 1


def test_upload_music(client: TestClient, workspace: Path):
    files = {"file": ("theme.mp3", b"fake-audio", "audio/mpeg")}
    res = client.post("/api/upload", files=files, data={"kind": "music"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["kind"] == "music"
    assert body["state"]["bgm"] is not None
    assert Path(body["music"]["path"]).is_file()


def test_clips_split_remove_and_move(client: TestClient):
    _upload_video(client)
    res = client.post(
        "/api/clips",
        json={"action": "split", "clip_index": 1, "split_at_sec": 4.0},
    )
    assert res.status_code == 200
    assert res.json()["state"]["clip_count"] == 2

    res = client.post(
        "/api/clips",
        json={"action": "move", "from_index": 1, "to_index": 2},
    )
    assert res.status_code == 200
    assert res.json()["state"]["clip_count"] == 2

    res = client.post("/api/clips", json={"action": "remove", "clip_index": 2})
    assert res.status_code == 200
    assert res.json()["state"]["clip_count"] == 1


def test_effects_zoom_text_transition(client: TestClient):
    _upload_video(client)
    # Tarvitaan kaksi klippiä siirtymään
    client.post(
        "/api/clips",
        json={"action": "split", "clip_index": 1, "split_at_sec": 5.0},
    )

    zoom = client.post(
        "/api/effects",
        json={
            "action": "zoom",
            "clip_index": 1,
            "start_sec": 0.0,
            "duration": 1.0,
            "target_x": 0.4,
            "target_y": 0.6,
            "zoom_factor": 2.0,
        },
    )
    assert zoom.status_code == 200
    effects = zoom.json()["state"]["clips"][0]["effects"]
    assert any(e["type"] == "zoom" for e in effects)

    text = client.post(
        "/api/effects",
        json={
            "action": "text",
            "clip_index": 2,
            "text": "Hei",
            "start_sec": 0.0,
            "duration": 1.5,
            "position": "top",
        },
    )
    assert text.status_code == 200
    assert any(
        e["type"] == "text" for e in text.json()["state"]["clips"][1]["effects"]
    )

    tr = client.post(
        "/api/effects",
        json={
            "action": "transition",
            "after_clip": 1,
            "transition_type": "fadeblack",
            "duration": 0.4,
        },
    )
    assert tr.status_code == 200
    assert tr.json()["state"]["transitions"][0]["after_clip"] == 1


def test_undo_redo(client: TestClient):
    _upload_video(client)
    before = client.get("/api/state").json()["state"]["clip_count"]
    assert before == 1

    undone = client.post("/api/undo")
    assert undone.status_code == 200
    # Upload tekee import + add_clip → yksi undo poistaa klipin
    assert undone.json()["changed"] is True

    redone = client.post("/api/redo")
    assert redone.status_code == 200
    assert redone.json()["changed"] is True


def test_render_preview_and_media_range(client: TestClient, workspace: Path):
    _upload_video(client)
    res = client.post("/api/render", json={"mode": "preview"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["url"] == "/media/cutsmith_preview.mp4"
    assert (workspace / "cutsmith_preview.mp4").is_file()

    media = client.get("/media/cutsmith_preview.mp4")
    assert media.status_code == 200
    assert media.content.startswith(b"\x00\x00\x00\x18")

    ranged = client.get(
        "/media/cutsmith_preview.mp4",
        headers={"Range": "bytes=0-3"},
    )
    assert ranged.status_code == 206
    assert ranged.headers.get("content-range", "").startswith("bytes 0-3/")
    assert len(ranged.content) == 4


def test_chat_stub_ready_for_phase3(client: TestClient):
    res = client.post("/api/chat", json={"message": "Lisää zoom klippiin 1", "lang": "fi"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["ready"] is False
    assert body["lang"] == "fi"
    assert "ei ole vielä kytketty" in body["reply"].lower()
    assert body["echo"] == "Lisää zoom klippiin 1"
    assert body["state"]["lang"] == "fi"

    en = client.post(
        "/api/chat",
        json={"message": "Add zoom to clip 1", "lang": "en"},
    )
    assert en.status_code == 200
    en_body = en.json()
    assert en_body["lang"] == "en"
    assert "not connected yet" in en_body["reply"].lower()
    assert en_body["state"]["lang"] == "en"


def test_static_index_served(client: TestClient):
    res = client.get("/")
    assert res.status_code == 200
    assert "CutSmith" in res.text
    assert 'id="preview"' in res.text
    assert 'data-lang="fi"' in res.text
    assert 'data-lang="en"' in res.text

    css = client.get("/style.css")
    assert css.status_code == 200
    assert "Syne" in css.text or "--accent" in css.text

    js = client.get("/app.js")
    assert js.status_code == 200
    assert "api/state" in js.text
    assert "i18n.js" in js.text

    i18n = client.get("/i18n.js")
    assert i18n.status_code == 200
    assert "cutsmith_lang" in i18n.text
    assert "Updating preview" in i18n.text


def test_clips_validation_error(client: TestClient):
    res = client.post("/api/clips", json={"action": "remove", "clip_index": 1})
    assert res.status_code == 400
    assert res.json()["ok"] is False


def test_media_path_traversal_blocked(client: TestClient):
    res = client.get("/media/../SPEC.md")
    assert res.status_code in (403, 404)
