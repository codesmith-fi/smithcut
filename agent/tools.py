"""EditorController-työkalut 1-pohjaisilla indekseillä (agentti + MCP)."""

from __future__ import annotations

import json
from typing import Any

from controller.editor import EditorController

# Ollama / OpenAI-tyyliset tool-skeemat
OLLAMA_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_state",
            "description": (
                "Return the current timeline summary. Clip and transition indices "
                "are 1-based (first clip is 1)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_clip",
            "description": (
                "Add a clip from an imported source. insert_at is optional 1-based "
                "position (1 = beginning)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "insert_at": {"type": "integer", "minimum": 1},
                },
                "required": ["source_id", "start_sec", "end_sec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trim_clip",
            "description": "Trim a clip. clip_index is 1-based.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_index": {"type": "integer", "minimum": 1},
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                },
                "required": ["clip_index", "start_sec", "end_sec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "split_clip",
            "description": (
                "Split a clip in two. clip_index is 1-based. "
                "split_at_sec is seconds from that clip's start (not source in_sec). "
                "Time codes like 2:20 / 2.20 mean MM:SS = 140 s on the timeline: "
                "find the clip covering T and use split_at_sec = T - timeline_start_sec. "
                "For 'Jaa video tästä kohdasta' / 'Split here', use the playhead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_index": {"type": "integer", "minimum": 1},
                    "split_at_sec": {"type": "number"},
                },
                "required": ["clip_index", "split_at_sec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_clip",
            "description": "Remove a clip. clip_index is 1-based.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_index": {"type": "integer", "minimum": 1},
                },
                "required": ["clip_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_clip",
            "description": (
                "Reorder clips. from_index and to_index are 1-based."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_index": {"type": "integer", "minimum": 1},
                    "to_index": {"type": "integer", "minimum": 1},
                },
                "required": ["from_index", "to_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_zoom",
            "description": (
                "Add a zoom effect to a clip. clip_index is 1-based. "
                "Optional target_x/target_y are 0–1; otherwise region is used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_index": {"type": "integer", "minimum": 1},
                    "start_sec": {"type": "number"},
                    "duration": {"type": "number"},
                    "region": {"type": "string"},
                    "target_x": {"type": "number"},
                    "target_y": {"type": "number"},
                    "zoom_factor": {"type": "number"},
                },
                "required": ["clip_index", "start_sec", "duration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_text",
            "description": (
                "Add on-screen text to a clip. clip_index is 1-based. "
                "font_style: clean|cinema|mono|impact. position: bottom|top|center."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_index": {"type": "integer", "minimum": 1},
                    "text": {"type": "string"},
                    "start_sec": {"type": "number"},
                    "duration": {"type": "number"},
                    "position": {"type": "string"},
                    "font_style": {
                        "type": "string",
                        "enum": ["clean", "cinema", "mono", "impact"],
                    },
                },
                "required": ["clip_index", "text", "start_sec", "duration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_transition",
            "description": (
                "Set a transition after a clip. after_clip is 1-based "
                "(1 = between clip 1 and 2). Types: fadeblack|fade|wipeleft|wiperight."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "after_clip": {"type": "integer", "minimum": 1},
                    "transition_type": {"type": "string"},
                    "duration": {"type": "number"},
                },
                "required": ["after_clip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_music",
            "description": "Set background music from a file path on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "bgm_volume": {"type": "number"},
                    "sfx_volume": {"type": "number"},
                    "fade_out_sec": {"type": "number"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo",
            "description": "Undo the last timeline change.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "redo",
            "description": "Redo the last undone timeline change.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cut_timeline_range",
            "description": (
                "Remove a time range from the timeline. "
                "If the user refers to the painted/selected range "
                "('valittu alue', 'maalattu kohta', 'selected range') without "
                "numbers, omit start_sec and duration_sec so the active "
                "selection is used. If only duration_sec is given, cut starts "
                "at the playhead. Explicit start_sec + duration_sec always win."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_sec": {
                        "type": "number",
                        "description": (
                            "How many seconds to remove. Omit together with "
                            "start_sec to cut the active selection."
                        ),
                    },
                    "start_sec": {
                        "type": "number",
                        "description": (
                            "Optional timeline start (seconds). "
                            "Omit with duration_sec to use the selection; "
                            "omit alone to start at playhead_sec."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_crossfade_to_selection",
            "description": (
                "Apply a crossfade/fade transition on the clip boundary inside "
                "the painted selection. Duration defaults to the selection "
                "length (capped). Use for 'Tee valitulle alueelle crossfade' "
                "or 'Apply a crossfade to the selected range'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transition_type": {
                        "type": "string",
                        "description": "fade|fadeblack|wipeleft|wiperight",
                    },
                    "duration_sec": {
                        "type": "number",
                        "description": "Optional override; defaults to selection length.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_blur_to_selection",
            "description": (
                "Add a soft focus-in/out blur (gblur) across the painted "
                "selection. Use for 'Tee valitulle alueelle pehmeä blur-siirtymä' "
                "or 'Add a soft blur transition to the selected range'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_sigma": {"type": "number"},
                    "fade_sec": {"type": "number"},
                },
                "required": [],
            },
        },
    },
]


def one_to_zero(index: int, *, label: str = "index") -> int:
    if not isinstance(index, int) or isinstance(index, bool):
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a 1-based integer") from exc
    if index < 1:
        raise ValueError(
            f"{label} must be 1-based (first clip/transition position is 1), got {index}"
        )
    return index - 1


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(), ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def execute_tool(
    editor: EditorController,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    lang: str = "fi",
) -> tuple[str, bool]:
    """Suorita työkalu. Palauttaa (tulos_teksti, muutti_aikajanaa)."""
    args = arguments or {}
    changed = True

    try:
        if name == "get_state":
            changed = False
            return _dump(editor.get_state_summary(lang=lang)), changed

        if name == "add_clip":
            insert_at = args.get("insert_at")
            zero_insert = one_to_zero(insert_at, label="insert_at") if insert_at is not None else None
            clip = editor.add_clip(
                args["source_id"],
                float(args["start_sec"]),
                float(args["end_sec"]),
                insert_at=zero_insert,
            )
            return _dump(clip), changed

        if name == "trim_clip":
            clip = editor.trim_clip(
                one_to_zero(args["clip_index"], label="clip_index"),
                float(args["start_sec"]),
                float(args["end_sec"]),
            )
            return _dump(clip), changed

        if name == "split_clip":
            left, right = editor.split_clip(
                one_to_zero(args["clip_index"], label="clip_index"),
                float(args["split_at_sec"]),
            )
            return _dump({"left": left.model_dump(), "right": right.model_dump()}), changed

        if name == "remove_clip":
            editor.remove_clip(one_to_zero(args["clip_index"], label="clip_index"))
            return json.dumps({"ok": True}), changed

        if name == "move_clip":
            editor.move_clip(
                one_to_zero(args["from_index"], label="from_index"),
                one_to_zero(args["to_index"], label="to_index"),
            )
            return json.dumps({"ok": True}), changed

        if name == "add_zoom":
            effect = editor.add_zoom(
                one_to_zero(args["clip_index"], label="clip_index"),
                start_sec=float(args["start_sec"]),
                duration=float(args["duration"]),
                region=str(args.get("region") or "center"),
                target_x=args.get("target_x"),
                target_y=args.get("target_y"),
                zoom_factor=float(args.get("zoom_factor") or 1.8),
            )
            return _dump(effect), changed

        if name == "add_text":
            effect = editor.add_text(
                one_to_zero(args["clip_index"], label="clip_index"),
                text=str(args["text"]),
                start_sec=float(args["start_sec"]),
                duration=float(args["duration"]),
                position=str(args.get("position") or "bottom"),
                font_style=str(args.get("font_style") or "clean"),
            )
            return _dump(effect), changed

        if name == "set_transition":
            transition = editor.set_transition(
                one_to_zero(args["after_clip"], label="after_clip"),
                transition_type=str(args.get("transition_type") or "fadeblack"),
                duration=float(args.get("duration") or 0.4),
            )
            return _dump(transition), changed

        if name == "set_music":
            music = editor.set_music(
                str(args["file_path"]),
                bgm_volume=float(args.get("bgm_volume") or 0.85),
                sfx_volume=float(args.get("sfx_volume") or 0.35),
                fade_out_sec=float(args.get("fade_out_sec") or 2.0),
            )
            return _dump(music), changed

        if name == "undo":
            ok = editor.undo()
            return json.dumps({"ok": ok}), ok

        if name == "redo":
            ok = editor.redo()
            return json.dumps({"ok": ok}), ok

        if name == "cut_timeline_range":
            start = args.get("start_sec")
            duration_arg = args.get("duration_sec")
            used_selection = False

            if start is None and duration_arg is None:
                if not editor.has_selection():
                    raise ValueError(
                        "no active selection; paint a range on the timeline "
                        "or provide start_sec and duration_sec"
                    )
                used_start = float(editor.selection_start_sec)  # type: ignore[arg-type]
                duration = float(editor.selection_end_sec) - used_start  # type: ignore[arg-type]
                editor.cut_selected_range()
                used_selection = True
            elif duration_arg is None:
                raise ValueError("duration_sec is required when start_sec is set")
            else:
                duration = float(duration_arg)
                if start is None:
                    used_start = editor.playhead_sec
                    editor.cut_at_playhead(duration)
                else:
                    used_start = float(start)
                    editor.cut_out_range(used_start, duration)

            return json.dumps(
                {
                    "ok": True,
                    "start_sec": used_start,
                    "duration_sec": duration,
                    "used_selection": used_selection,
                    "playhead_sec": editor.playhead_sec,
                    "selection_start_sec": editor.selection_start_sec,
                    "selection_end_sec": editor.selection_end_sec,
                }
            ), changed

        if name == "apply_crossfade_to_selection":
            transition = editor.apply_crossfade_to_selection(
                transition_type=str(args.get("transition_type") or "fade"),
                duration_sec=(
                    float(args["duration_sec"])
                    if args.get("duration_sec") is not None
                    else None
                ),
            )
            return _dump(transition), changed

        if name == "add_blur_to_selection":
            effects = editor.add_blur_to_selection(
                max_sigma=float(args.get("max_sigma") or 18.0),
                fade_sec=float(args.get("fade_sec") or 0.6),
            )
            return _dump([e.model_dump() for e in effects]), changed

        return json.dumps({"error": f"Unknown tool: {name}"}), False
    except Exception as exc:  # noqa: BLE001 – LLM tarvitsee selkeän virheen
        return json.dumps({"error": str(exc)}), False
    finally:
        try:
            editor._flush_persist()
        except Exception:  # noqa: BLE001 – persist ei saa peittää työkalun tulosta
            pass
