"""Regressiotestit pikakorjauksille ja vakauden parannuksille."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent.tools import execute_tool
from controller.editor import EditorController, PROJECT_STATE_FILENAME
from model.timeline import AudioClip, BlurEffect, Clip, TextEffect, Timeline, VideoSource
from web.api import create_app


PROBE_META = {
    "duration": 20.0,
    "width": 1280,
    "height": 720,
    "fps": 30.0,
    "has_audio": True,
}


def _editor_with_video(tmp_path: Path) -> EditorController:
    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake-video")
    editor = EditorController(workspace=tmp_path)
    with patch("controller.editor.probe", return_value=PROBE_META):
        src = editor.import_video(str(video))
    editor.add_clip(src.id, 0.0, 20.0)
    return editor


def test_split_audio_clip_via_execute_tool_serializes_json(tmp_path: Path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake-audio")
    tl = Timeline(
        audio_clips=[
            AudioClip(
                id="ac1",
                source_path=str(audio),
                in_point=0.0,
                out_point=10.0,
                timeline_start=0.0,
            )
        ]
    )
    editor = EditorController(workspace=tmp_path, timeline=tl)

    text, changed = execute_tool(editor, "split_audio_clip", {"timestamp_sec": 4.0})
    assert changed is True
    payload = json.loads(text)
    assert "error" not in payload
    assert payload["left"]["out_point"] == pytest.approx(4.0)
    assert payload["right"]["in_point"] == pytest.approx(4.0)
    assert payload["right"]["timeline_start"] == pytest.approx(4.0)


def test_split_clip_preserves_blur_end_sec_and_straddling_effects(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    # Blur kokonaan oikeassa puoliskossa (12..16s) + teksti jakokohdan yli (8..12s)
    editor.add_blur_effect(0, 12.0, 16.0, max_sigma=15.0, fade_sec=0.5)
    editor.add_text(0, "Rajateksti", 8.0, 4.0)

    left, right = editor.split_clip(0, 10.0)

    # Vasen klippi (0..10s): rajateksti trimmattu 8..10s
    assert len(left.effects) == 1
    assert isinstance(left.effects[0], TextEffect)
    assert left.effects[0].start_sec == pytest.approx(8.0)
    assert left.effects[0].duration == pytest.approx(2.0)

    # Oikea klippi (10..20s -> paikallinen 0..10s): blur siirtynyt 2..6s, teksti 0..2s
    blurs = [e for e in right.effects if isinstance(e, BlurEffect)]
    texts = [e for e in right.effects if isinstance(e, TextEffect)]
    assert len(blurs) == 1
    assert blurs[0].start_sec == pytest.approx(2.0)
    assert blurs[0].end_sec == pytest.approx(6.0)
    assert len(texts) == 1
    assert texts[0].start_sec == pytest.approx(0.0)
    assert texts[0].duration == pytest.approx(2.0)
    editor.timeline.validate_consistency()


def test_execute_tool_preserves_zero_values(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    music = tmp_path / "bg.mp3"
    music.write_bytes(b"fake-music")

    text, changed = execute_tool(
        editor,
        "set_music",
        {
            "file_path": str(music),
            "bgm_volume": 0.0,
            "sfx_volume": 0.0,
            "fade_out_sec": 0.0,
        },
    )
    assert changed is True
    data = json.loads(text)
    assert data["bgm_volume"] == pytest.approx(0.0)
    assert data["sfx_volume"] == pytest.approx(0.0)
    assert data["fade_out_sec"] == pytest.approx(0.0)


def test_serve_media_blocks_sibling_prefix_traversal(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    evil = tmp_path / "workspace_secret"
    evil.mkdir()
    (evil / "secret.txt").write_text("top-secret", encoding="utf-8")

    editor = EditorController(workspace=ws)
    app = create_app(workspace=ws, editor=editor)
    with TestClient(app) as client:
        res = client.get("/media/../workspace_secret/secret.txt")
        assert res.status_code in (403, 404)
        assert "top-secret" not in res.text


def test_remove_transition_controller_api_and_tool(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    editor.split_clip(0, 10.0)
    editor.set_transition(0, "fade", 1.0)
    assert len(editor.timeline.transitions) == 1

    # Poisto työkalun kautta
    text, changed = execute_tool(editor, "remove_transition", {"after_clip": 1})
    assert changed is True
    assert json.loads(text)["removed"] is True
    assert editor.timeline.transitions == []

    # Undo palauttaa siirtymän
    assert editor.undo() is True
    assert len(editor.timeline.transitions) == 1

    # Poisto Web API:n kautta
    app = create_app(workspace=tmp_path, editor=editor)
    with TestClient(app) as client:
        res = client.post(
            "/api/effects",
            json={"action": "remove_transition", "after_clip": 1},
        )
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert res.json()["state"]["transitions"] == []


def test_trim_clip_prunes_invalid_adjacent_transitions(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    editor.split_clip(0, 10.0)
    editor.set_transition(0, "fade", 2.0)
    assert len(editor.timeline.transitions) == 1

    # Trimmaa klippi 0 lyhyemmäksi kuin 2.0 s siirtymä
    editor.trim_clip(0, 0.0, 1.5)
    assert editor.timeline.transitions == []
    editor.timeline.validate_consistency()


def test_mutations_persist_without_get_state_summary(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    state_path = tmp_path / PROJECT_STATE_FILENAME
    assert state_path.is_file()

    # Poista tiedosto ja varmista ettei get_state_summary kirjoita levylle
    state_path.unlink()
    editor.get_state_summary()
    assert not state_path.exists()


def test_concurrent_mutations_thread_safe(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    errors: list[Exception] = []

    def worker(offset: int) -> None:
        try:
            for i in range(10):
                editor.set_playhead((offset + i) % 15)
                editor.get_state_summary()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert (tmp_path / PROJECT_STATE_FILENAME).is_file()


def test_build_command_uses_input_level_ss_and_t():
    from compiler.ffmpeg import build_command

    src = VideoSource(
        id="src1",
        path="/videos/long.mp4",
        duration=120.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[
            Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=5.0),
            Clip(id="c1", source_id="src1", in_sec=45.0, out_sec=52.5),
        ],
    )
    cmd = build_command(tl, "/tmp/out.mp4", mode="preview")
    # Ensimmäiselle klipille (in_sec=0) vain -t 5.000000 -i ...
    # Toiselle klipille (in_sec=45) -ss 45.000000 -t 7.500000 -i ...
    assert "-ss" in cmd
    ss_idx = cmd.index("-ss")
    assert cmd[ss_idx + 1] == "45.000000"
    assert cmd[ss_idx + 2] == "-t"
    assert cmd[ss_idx + 3] == "7.500000"
    assert cmd[ss_idx + 4] == "-i"
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=0:end=7.500000" in fc
    assert "atrim=start=0:end=7.500000" in fc


def test_crossfade_tool_enforces_one_based_clip_index(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    editor.split_clip(0, 10.0)

    bad_text, bad_changed = execute_tool(
        editor,
        "apply_crossfade_to_selection",
        {"clip_index": 0, "duration_sec": 2.0},
    )
    assert bad_changed is False
    assert "1-based" in json.loads(bad_text)["error"]

    ok_text, ok_changed = execute_tool(
        editor,
        "apply_crossfade_to_selection",
        {"clip_index": 1, "duration_sec": 2.0},
    )
    assert ok_changed is True
    payload = json.loads(ok_text)
    assert payload["after_clip"] == 1
    assert "after clip 1" in payload["message"]


def test_run_agent_multi_turn_history_and_keep_alive_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from unittest.mock import MagicMock

    from agent.llm_agent import run_agent

    monkeypatch.setenv("CUTSMITH_KEEP_ALIVE", "15s")
    editor = _editor_with_video(tmp_path)
    history: list[dict[str, str]] = []

    mock_client = MagicMock()
    resp1 = MagicMock(status_code=200)
    resp1.json.return_value = {
        "message": {"role": "assistant", "content": "Eka vastaus."}
    }
    resp2 = MagicMock(status_code=200)
    resp2.json.return_value = {
        "message": {"role": "assistant", "content": "Toinen vastaus."}
    }
    mock_client.post.side_effect = [resp1, resp2]

    run_agent(editor, "Eka kysymys", client=mock_client, history=history)
    assert len(history) == 2

    run_agent(editor, "Jatkokysymys", client=mock_client, history=history)
    assert len(history) == 4

    second_payload = mock_client.post.call_args_list[1].kwargs["json"]
    assert second_payload["keep_alive"] == "15s"
    roles_and_contents = [
        (m["role"], m["content"]) for m in second_payload["messages"][1:]
    ]
    assert roles_and_contents == [
        ("user", "Eka kysymys"),
        ("assistant", "Eka vastaus."),
        ("user", "Jatkokysymys"),
    ]


def test_cut_out_range_preserves_untouched_transitions(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    # 3 klippiä: 0..8s, 8..14s, 14..20s
    editor.split_clip(0, 8.0)
    editor.split_clip(1, 6.0)
    editor.set_transition(0, "fade", 1.0)
    editor.set_transition(1, "fadeblack", 1.0)

    # Leikkaa pala keskeltä klippiä 0 (2..4s) -> 4 klippiä, molemmat siirtymät säilyvät!
    editor.cut_out_range(2.0, 2.0)
    assert len(editor.timeline.clips) == 4
    assert [(t.after_clip_index, t.type) for t in editor.timeline.transitions] == [
        (1, "fade"),
        (2, "fadeblack"),
    ]
    editor.timeline.validate_consistency()


def test_cut_out_range_across_seam_drops_only_cut_transition(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    editor.split_clip(0, 8.0)
    editor.split_clip(1, 6.0)
    editor.set_transition(0, "fade", 1.0)
    editor.set_transition(1, "wipeleft", 1.0)

    # Leikkaa sauman 0->1 yli (6..9s): ensimmäinen siirtymä poistuu, toinen (wipeleft) säilyy!
    editor.cut_out_range(6.0, 3.0)
    assert [(t.after_clip_index, t.type) for t in editor.timeline.transitions] == [
        (1, "wipeleft"),
    ]
    editor.timeline.validate_consistency()


def test_move_clip_preserves_adjacent_pair_transitions(tmp_path: Path):
    editor = _editor_with_video(tmp_path)
    # 4 klippiä: C0, C1, C2, C3 (5s kukin)
    editor.split_clip(0, 5.0)
    editor.split_clip(1, 5.0)
    editor.split_clip(2, 5.0)
    editor.set_transition(0, "fade", 0.5)       # C0 -> C1
    editor.set_transition(2, "fadeblack", 0.5)  # C2 -> C3

    # Siirrä C3 alkuun -> [C3, C0, C1, C2]: C0->C1 pysyy vierekkäin indeksissä 1
    editor.move_clip(3, 0)
    assert [(t.after_clip_index, t.type) for t in editor.timeline.transitions] == [
        (1, "fade"),
    ]
    editor.timeline.validate_consistency()
