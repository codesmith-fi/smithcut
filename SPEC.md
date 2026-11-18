# SPEC: CutSmith – Paikallinen AI-avusteinen FFmpeg-videoeditori

## 1. Yleiskuvaus ja tavoite
CutSmith on paikallinen, deterministinen videoeditointityökalu, joka yhdistää FFmpeg-suodatingraafit, FastMCP-työkalurajapinnan ja paikallisen kielimallin (Ollama / `qwen3:8b`, 8 Gt VRAM). Käyttäjä ohjaa leikkauksia, siirtymiä, zoomauksia ja tekstityksiä luonnollisella kielellä tai suoraan MCP-työkalukutsuilla ilman pilvipalveluita tai raskaita GUI-editoreita.

## 2. Arkkitehtuuri (tiukka MVC-jaottelu)
- **Model (`model/`):** Puhdas Pydantic v2 -datamalli (`Timeline`), joka kuvaa projektin tilan deterministisenä JSON-rakenteena. Ei FFmpeg-, aliprosessi- tai UI-riippuvuuksia. Sisältää `HistoryManager`-luokan (Undo/Redo-tilannekuvat).
- **Compiler (`compiler/`):** Lukee `Timeline`-olion, validoi aikaleimat ja kääntää sen deterministiseksi FFmpeg `filter_complex` -komennoksi. Pakottaa aina ympäristömuuttujan `LC_ALL=C` kaikissa aliprosesseissa. Tukee kahta renderöintiprofiilia:
  - `preview`: 1280x720, `h264_nvenc` (`-preset p1`), nopea esikatselu.
  - `final`: Natiiviresoluutio (1920x1080 tai lähteen mukaan), korkealaatuinen enkoodaus (`h264_nvenc` `-cq 19` tai `libx264` `-crf 18`).
- **Controller (`controller/`):**
  - `editor.py`: `EditorController` suorittaa atomiset editointioperaatiot `Timeline`-malliin ja tallentaa jokaisen muutoksen `HistoryManager`-pinoon.
  - `mcp_server.py`: Altistaa `EditorController`-metodit FastMCP-työkaluina.
  - `llm_agent.py`: Keskustelee paikallisen Ollama API:n (`http://localhost:11434/api/chat`) kanssa käyttäen tool calling -rajapintaa. Asettaa `"keep_alive": "15s"`, jotta VRAM vapautuu nopeasti NVENC-renderöinnille.
- **View (`web/`):** Kevyt FastAPI-palvelin (`web/api.py`) ja yksisivuinen tumma HTML/JS-käyttöliittymä (`web/static/index.html`, `web/static/app.js`), jossa on HTML5 `<video>`-esikatselusoitin, visuaalinen klippilista/aikajana ID-numeroineen, prompt-chat, Undo/Redo-napit sekä Render Preview / Export Final -toiminnot.

## 3. Datamalli (`model/timeline.py`)
- `VideoSource`: `id: str`, `path: str`, `duration: float`, `width: int`, `height: int`, `fps: float`, `has_audio: bool`
- `ZoomEffect`: `type: Literal["zoom"] = "zoom"`, `start_sec: float`, `duration: float`, `target_x: float` (0.0–1.0), `target_y: float` (0.0–1.0), `zoom_factor: float` (1.1–4.0)
- `TextEffect`: `type: Literal["text"] = "text"`, `text: str`, `start_sec: float`, `duration: float`, `position: Literal["bottom", "top", "center"] = "bottom"`, `font_size: int = 36`, `color: str = "white"`
- `Clip`: `id: str`, `source_id: str`, `in_sec: float`, `out_sec: float`, `speed: float = 1.0`, `effects: list[ZoomEffect | TextEffect] = []`
- `Transition`: `after_clip_index: int`, `type: Literal["fadeblack", "fade", "wipeleft", "wiperight"] = "fadeblack"`, `duration: float = 0.4`
- `BackgroundMusic`: `path: str`, `bgm_volume: float = 0.85`, `sfx_volume: float = 0.35`, `fade_out_sec: float = 2.0`
- `Timeline`: `sources: dict[str, VideoSource]`, `clips: list[Clip]`, `transitions: list[Transition]`, `bgm: Optional[BackgroundMusic]`, `output_fps: int = 60`

## 4. Controller- ja MCP-työkalut (`controller/editor.py`)
1. `import_video(file_path: str) -> VideoSource`
2. `add_clip(source_id: str, start_sec: float, end_sec: float, insert_at: int | None = None) -> Clip`
3. `trim_clip(clip_index: int, start_sec: float, end_sec: float) -> Clip`
4. `split_clip(clip_index: int, split_at_sec: float) -> tuple[Clip, Clip]`
5. `remove_clip(clip_index: int) -> None`
6. `set_transition(after_clip_index: int, transition_type: str = "fadeblack", duration: float = 0.4) -> Transition`
7. `add_zoom(clip_index: int, start_sec: float, duration: float, region: str = "center", target_x: float | None = None, target_y: float | None = None, zoom_factor: float = 1.8) -> ZoomEffect`
8. `add_text(clip_index: int, text: str, start_sec: float, duration: float, position: str = "bottom") -> TextEffect`
9. `set_music(file_path: str, bgm_volume: float = 0.85, sfx_volume: float = 0.35, fade_out_sec: float = 2.0) -> BackgroundMusic`
10. `undo() -> bool` / `redo() -> bool`
11. `get_state_summary() -> dict` (palauttaa tiiviin yhteenvedon klipeistä, indekseistä, kestoista ja efekteistä LLM:ää ja UI:ta varten)
12. `render(mode: str = "preview") -> str` (palauttaa renderöidyn videotiedoston polun)

## 5. Toteutusvaiheet (suorita yksi vaihe kerrallaan)
- **Vaihe 1 (Core & Compiler):** Toteuta `model/timeline.py`, `model/history.py`, `compiler/ffprobe.py`, `compiler/ffmpeg.py` ja `controller/editor.py`. Kirjoita kattavat `pytest`-yksikkötestit (`tests/`), jotka testaavat aikajanan rakentamisen, validoinnin, undo/redo-toiminnot sekä 3 klipin `xfade` + `zoompan` + `drawtext` -suodatingraafin generoinnin.
- **Vaihe 2 (Web UI & API):** Toteuta `web/api.py` (FastAPI) ja `web/static/index.html` + `app.js`. Käyttöliittymässä näkyy esikatseluvideo, aikajanan klipit indekseineen (`#0`, `#1`, `#2`...), efektit, Undo/Redo, Render Preview / Export Final -napit sekä tekstikenttä komennoille.
- **Vaihe 3 (Paikallinen Qwen + MCP):** Toteuta `controller/llm_agent.py` (Ollama `qwen3:8b` tool calling) ja `controller/mcp_server.py` (FastMCP). Kytke prompt-kenttä kutsumaan paikallista Qwen-agenttia, joka suorittaa työkalukutsut ja laukaisee esikatselun renderöinnin.
- **Vaihe 4 (Visuaalinen kohdistus - VLM):** Lisää avainruutujen poiminta ja valinnainen paikallinen VLM-kysely, jolla ruudulla näkyvien UI-elementtien koordinaatit voidaan paikantaa nimeltä.