"""FastMCP/MCPServer – generoitu automaattisesti agent.tools.OLLAMA_TOOLS -rekisteristä."""

from __future__ import annotations

import inspect
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import MCPServer

from agent.tools import OLLAMA_TOOLS, execute_tool
from controller.editor import EditorController

# Alias: mcp 2.x nimesi FastMCP → MCPServer
FastMCP = MCPServer

_INDEX_HINT = (
    " IMPORTANT: All clip_index / after_clip / from_index / to_index / insert_at "
    "values are 1-based (the first clip is 1)."
)

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


def _make_tool_handler(
    ctrl: EditorController,
    tool_name: str,
    parameters_schema: dict[str, Any],
    *,
    lang: str,
):
    """Luo MCP-työkalufunktio JSON-skeemasta oikealla inspect.Signaturella."""
    props: dict[str, Any] = parameters_schema.get("properties") or {}
    required: set[str] = set(parameters_schema.get("required") or [])

    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": str}

    # Pakolliset parametrit ensin, valinnaiset oletusarvolla None
    ordered_keys = [k for k in props if k in required] + [
        k for k in props if k not in required
    ]
    for key in ordered_keys:
        prop = props[key]
        py_type = _JSON_TYPE_MAP.get(prop.get("type", ""), Any)
        if key in required:
            sig_params.append(
                inspect.Parameter(
                    key,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=py_type,
                )
            )
            annotations[key] = py_type
        else:
            opt_type = py_type | None if py_type is not Any else Any
            sig_params.append(
                inspect.Parameter(
                    key,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None,
                    annotation=opt_type,
                )
            )
            annotations[key] = opt_type

    def _handler(**kwargs: Any) -> str:
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        text, _changed = execute_tool(ctrl, tool_name, cleaned, lang=lang)
        return text

    _handler.__name__ = tool_name
    _handler.__annotations__ = annotations
    _handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=sig_params,
        return_annotation=str,
    )
    return _handler


def create_mcp_server(
    editor: EditorController | None = None,
    *,
    lang: str = "fi",
) -> MCPServer:
    """Luo MCP-palvelin joka rekisteröi kaikki OLLAMA_TOOLS-työkalut yhdestä lähteestä."""
    ctrl = editor or EditorController()
    server = MCPServer(
        name="CutSmith",
        instructions=(
            "CutSmith local video editor tools. "
            "Never invent FFmpeg commands; only use these tools. "
            "Clip and transition indices are always 1-based."
        ),
    )

    for entry in OLLAMA_TOOLS:
        fn_spec = entry.get("function") or {}
        name = fn_spec["name"]
        desc = fn_spec.get("description") or ""
        if "1-based" not in desc:
            desc = desc + _INDEX_HINT
        params = fn_spec.get("parameters") or {"type": "object", "properties": {}}
        handler = _make_tool_handler(ctrl, name, params, lang=lang)
        server.tool(name=name, description=desc)(handler)

    # Testejä / agenttia varten: säilytä controller-viite
    server._cutsmith_editor = ctrl  # type: ignore[attr-defined]
    return server


def main() -> None:
    """Käynnistä MCP stdio-tilassa."""
    create_mcp_server().run()


if __name__ == "__main__":
    main()
