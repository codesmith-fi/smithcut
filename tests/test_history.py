"""HistoryManager Undo/Redo -testit."""

from __future__ import annotations

from model.history import HistoryManager
from model.timeline import Clip, Timeline, VideoSource


def _timeline_with_n_clips(n: int) -> Timeline:
    src = VideoSource(
        id="src1",
        path="/tmp/a.mp4",
        duration=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    clips = [
        Clip(id=f"c{i}", source_id="src1", in_sec=float(i), out_sec=float(i + 1))
        for i in range(n)
    ]
    return Timeline(sources={"src1": src}, clips=clips)


def test_push_enables_undo_and_clears_redo():
    hist = HistoryManager()
    current = _timeline_with_n_clips(1)
    hist.push(current)
    assert hist.can_undo()
    assert not hist.can_redo()

    hist._redo.append(_timeline_with_n_clips(2))  # noqa: SLF001 – testaa clear
    hist.push(current)
    assert hist.redo_depth == 0


def test_undo_redo_roundtrip():
    hist = HistoryManager()
    t0 = _timeline_with_n_clips(0)
    t1 = _timeline_with_n_clips(1)
    t2 = _timeline_with_n_clips(2)

    hist.push(t0)
    current = t1
    hist.push(current)
    current = t2

    restored = hist.undo(current)
    assert restored is not None
    assert len(restored.clips) == 1
    current = restored

    restored = hist.undo(current)
    assert restored is not None
    assert len(restored.clips) == 0
    current = restored

    assert hist.undo(current) is None

    restored = hist.redo(current)
    assert restored is not None
    assert len(restored.clips) == 1
    current = restored

    restored = hist.redo(current)
    assert restored is not None
    assert len(restored.clips) == 2


def test_snapshots_are_deep_copies():
    hist = HistoryManager()
    tl = _timeline_with_n_clips(1)
    hist.push(tl)
    tl.clips[0].out_sec = 99.0
    # Pushattu tilannekuva ei muutu
    assert hist._undo[0].clips[0].out_sec == 1.0  # noqa: SLF001


def test_max_depth_trims_oldest():
    hist = HistoryManager(max_depth=2)
    for n in range(4):
        hist.push(_timeline_with_n_clips(n))
    assert hist.undo_depth == 2
    # Vanhimmat (0 ja 1 klippiä) tippuivat → jäljellä 2 ja 3
    assert len(hist._undo[0].clips) == 2  # noqa: SLF001
    assert len(hist._undo[1].clips) == 3  # noqa: SLF001
