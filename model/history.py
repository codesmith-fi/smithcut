"""Undo/Redo-tilannekuva-pino Timeline-malleille."""

from __future__ import annotations

from model.timeline import Timeline


class HistoryManager:
    """Hallitsee Undo/Redo-tilannekuvia deterministisesti.

    Ennen jokaista mutaatiota kutsutaan ``push(current)``, jolloin nykyinen
    tila tallennetaan undo-pinoon ja redo-pino tyhjennetään.
    """

    def __init__(self, max_depth: int = 100) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        self._max_depth = max_depth
        self._undo: list[Timeline] = []
        self._redo: list[Timeline] = []

    def push(self, timeline: Timeline) -> None:
        """Tallenna tilannekuva ennen muutosta; tyhjennä redo."""
        snapshot = timeline.model_copy(deep=True)
        self._undo.append(snapshot)
        if len(self._undo) > self._max_depth:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, current: Timeline) -> Timeline | None:
        """Palauta edellinen tila ja siirrä nykyinen redo-pinoon."""
        if not self._undo:
            return None
        self._redo.append(current.model_copy(deep=True))
        return self._undo.pop()

    def redo(self, current: Timeline) -> Timeline | None:
        """Palauta seuraava redo-tila ja siirrä nykyinen undo-pinoon."""
        if not self._redo:
            return None
        self._undo.append(current.model_copy(deep=True))
        if len(self._undo) > self._max_depth:
            self._undo.pop(0)
        return self._redo.pop()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)
