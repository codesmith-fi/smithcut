"""FastMCP/MCPServer – EditorController-työkalut 1-pohjaisilla indekseillä."""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import MCPServer

from agent.tools import execute_tool
from controller.editor import EditorController

# Alias: mcp 2.x nimesi FastMCP → MCPServer
FastMCP = MCPServer

_INDEX_HINT = (
    " IMPORTANT: All clip_index / after_clip / from_index / to_index / insert_at "
    "values are 1-based (the first clip is 1)."
)


def create_mcp_server(
    editor: EditorController | None = None,
    *,
    lang: str = "fi",
) -> MCPServer:
    """Luo MCP-palvelin joka käyttää annettua EditorController-instanssia."""
    ctrl = editor or EditorController()
    server = MCPServer(
        name="CutSmith",
        instructions=(
            "CutSmith local video editor tools. "
            "Never invent FFmpeg commands; only use these tools. "
            "Clip and transition indices are always 1-based."
        ),
    )

    def _run(name: str, arguments: dict[str, Any]) -> str:
        text, _changed = execute_tool(ctrl, name, arguments, lang=lang)
        return text

    @server.tool(
        name="get_state",
        description="Return the timeline summary." + _INDEX_HINT,
    )
    def get_state() -> str:
        return _run("get_state", {})

    @server.tool(
        name="add_clip",
        description="Add a clip from a source. insert_at is optional 1-based." + _INDEX_HINT,
    )
    def add_clip(
        source_id: str,
        start_sec: float,
        end_sec: float,
        insert_at: int | None = None,
    ) -> str:
        args: dict[str, Any] = {
            "source_id": source_id,
            "start_sec": start_sec,
            "end_sec": end_sec,
        }
        if insert_at is not None:
            args["insert_at"] = insert_at
        return _run("add_clip", args)

    @server.tool(
        name="trim_clip",
        description="Trim a clip by source in/out times. clip_index is 1-based." + _INDEX_HINT,
    )
    def trim_clip(clip_index: int, start_sec: float, end_sec: float) -> str:
        return _run(
            "trim_clip",
            {
                "clip_index": clip_index,
                "start_sec": start_sec,
                "end_sec": end_sec,
            },
        )

    @server.tool(
        name="split_clip",
        description="Split a clip at a time from the clip start. clip_index is 1-based."
        + _INDEX_HINT,
    )
    def split_clip(clip_index: int, split_at_sec: float) -> str:
        return _run(
            "split_clip",
            {"clip_index": clip_index, "split_at_sec": split_at_sec},
        )

    @server.tool(
        name="remove_clip",
        description="Remove a clip. clip_index is 1-based." + _INDEX_HINT,
    )
    def remove_clip(clip_index: int) -> str:
        return _run("remove_clip", {"clip_index": clip_index})

    @server.tool(
        name="move_clip",
        description="Move a clip to a new 1-based position." + _INDEX_HINT,
    )
    def move_clip(from_index: int, to_index: int) -> str:
        return _run(
            "move_clip",
            {"from_index": from_index, "to_index": to_index},
        )

    @server.tool(
        name="add_zoom",
        description="Add a zoom effect. clip_index is 1-based." + _INDEX_HINT,
    )
    def add_zoom(
        clip_index: int,
        start_sec: float,
        duration: float,
        region: str = "center",
        target_x: float | None = None,
        target_y: float | None = None,
        zoom_factor: float = 1.8,
    ) -> str:
        args: dict[str, Any] = {
            "clip_index": clip_index,
            "start_sec": start_sec,
            "duration": duration,
            "region": region,
            "zoom_factor": zoom_factor,
        }
        if target_x is not None:
            args["target_x"] = target_x
        if target_y is not None:
            args["target_y"] = target_y
        return _run("add_zoom", args)

    @server.tool(
        name="add_text",
        description=(
            "Add text overlay. clip_index is 1-based. "
            "font_style: clean|cinema|mono|impact."
        )
        + _INDEX_HINT,
    )
    def add_text(
        clip_index: int,
        text: str,
        start_sec: float,
        duration: float,
        position: str = "bottom",
        font_style: str = "clean",
    ) -> str:
        return _run(
            "add_text",
            {
                "clip_index": clip_index,
                "text": text,
                "start_sec": start_sec,
                "duration": duration,
                "position": position,
                "font_style": font_style,
            },
        )

    @server.tool(
        name="set_transition",
        description=(
            "Set transition after a clip. after_clip is 1-based "
            "(1 = between clips 1 and 2)."
        )
        + _INDEX_HINT,
    )
    def set_transition(
        after_clip: int,
        transition_type: str = "fadeblack",
        duration: float = 0.4,
    ) -> str:
        return _run(
            "set_transition",
            {
                "after_clip": after_clip,
                "transition_type": transition_type,
                "duration": duration,
            },
        )

    @server.tool(
        name="set_music",
        description="Set background music from a local file path.",
    )
    def set_music(
        file_path: str,
        bgm_volume: float = 0.85,
        sfx_volume: float = 0.35,
        fade_out_sec: float = 2.0,
    ) -> str:
        return _run(
            "set_music",
            {
                "file_path": file_path,
                "bgm_volume": bgm_volume,
                "sfx_volume": sfx_volume,
                "fade_out_sec": fade_out_sec,
            },
        )

    @server.tool(name="undo", description="Undo the last edit.")
    def undo() -> str:
        return _run("undo", {})

    @server.tool(name="redo", description="Redo the last undone edit.")
    def redo() -> str:
        return _run("redo", {})

    @server.tool(
        name="cut_timeline_range",
        description=(
            "Remove a time range from the timeline. "
            "Omit both start_sec and duration_sec to cut the painted/selected "
            "range ('valittu alue' / 'selected range'). "
            "If only duration_sec is given, cut starts at the playhead. "
            "Explicit start_sec + duration_sec always win."
        ),
    )
    def cut_timeline_range(
        duration_sec: float | None = None,
        start_sec: float | None = None,
    ) -> str:
        args: dict[str, Any] = {}
        if duration_sec is not None:
            args["duration_sec"] = duration_sec
        if start_sec is not None:
            args["start_sec"] = start_sec
        return _run("cut_timeline_range", args)

    @server.tool(
        name="apply_crossfade_to_selection",
        description=(
            "Apply a crossfade on the clip seam inside the painted selection. "
            "Duration defaults to the selection length (capped). "
            "Use for 'Tee valitulle alueelle crossfade' / "
            "'Apply a crossfade to the selected range'."
        ),
    )
    def apply_crossfade_to_selection(
        transition_type: str = "fade",
        duration_sec: float | None = None,
    ) -> str:
        args: dict[str, Any] = {"transition_type": transition_type}
        if duration_sec is not None:
            args["duration_sec"] = duration_sec
        return _run("apply_crossfade_to_selection", args)

    @server.tool(
        name="add_blur_to_selection",
        description=(
            "Add a soft focus-in/out blur across the painted selection. "
            "Use for 'Tee valitulle alueelle pehmeä blur-siirtymä' / "
            "'Add a soft blur transition to the selected range'."
        ),
    )
    def add_blur_to_selection(
        max_sigma: float = 18.0,
        fade_sec: float = 0.6,
    ) -> str:
        return _run(
            "add_blur_to_selection",
            {"max_sigma": max_sigma, "fade_sec": fade_sec},
        )

    # Testejä / agenttia varten: säilytä controller-viite
    server._cutsmith_editor = ctrl  # type: ignore[attr-defined]
    return server


def main() -> None:
    """Käynnistä MCP stdio-tilassa."""
    create_mcp_server().run()


if __name__ == "__main__":
    main()
