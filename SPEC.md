# SPEC: CutSmith – Paikallinen AI-avusteinen FFmpeg-videoeditori

## 1. Yleiskuvaus ja tavoite
CutSmith on paikallinen, ei-tuhoava ja deterministinen videoeditointityökalu, joka yhdistää FFmpeg-suodatingraafit, MCP-työkalurajapinnan ja paikallisen kielimallin (Ollama / `qwen3:8b`). Käyttäjä ohjaa leikkauksia, siirtymiä, zoomauksia, sumennuksia, tekstityksiä sekä erillistä ääniaikajanaa luonnollisella kielellä (FI/EN), selainkäyttöliittymällä tai suoraan MCP-työkalukutsuilla ilman pilvipalveluita.

## 2. Arkkitehtuuri (tiukka MVC-jaottelu)
- **Model (`model/`):**
  - `timeline.py`: Puhdas Pydantic v2 -datamalli (`Timeline`, `VideoSource`, `Clip`, `ZoomEffect`, `TextEffect`, `BlurEffect`, `Transition`, `BackgroundMusic`, `AudioTrack`, `AudioClip`). Ei FFmpeg-, aliprosessi- tai UI-riippuvuuksia.
  - `history.py`: `HistoryManager` (deterministinen Undo/Redo-tilannekuvapino syväkopioilla).
- **Compiler (`compiler/`):**
  - `ffprobe.py`: Metadatan luku video- ja äänitiedostoille (`LC_ALL=C`).
  - `ffmpeg.py`: Kääntää `Timeline`-olion deterministiseksi FFmpeg `filter_complex` -komennoksi input-tason `-ss`/`-t`-haulla, pakottaa `LC_ALL=C`, lukee edistymisen `-progress pipe:1` -virrasta ja poimii still-ruudut, pikkukuvat sekä erotetut ääniraidat. Kaksi renderöintiprofiilia:
    - `preview`: 480p (parillinen leveys kuvasuhteen mukaan, max 30 fps), `libx264` (`-preset ultrafast -crf 28`), nopea ja ennustettava esikatselu.
    - `export` (`final`): Natiiviresoluutio ja natiivi FPS, korkealaatuinen enkoodaus (`h264_nvenc` `-preset p4 -cq 18` jos käytettävissä, muuten `libx264` `-preset medium -crf 18`).
- **Controller (`controller/`):**
  - `editor.py`: Säieturvallinen (`RLock`) `EditorController`-fasadi, joka suorittaa atomiset editointioperaatiot `Timeline`-malliin ja tallentaa tilan automaattisesti `_persist_batch`-kontekstimanagerin kautta.
  - `persistence.py`: Atominen `workspace/project_state.json` -tallennus/lataus ja `reset_project`-siivous.
  - `media_cache.py`: Still-ruutujen ja klippi-pikkukuvien välimuistitus (`workspace/frames/`, `workspace/thumbnails/`).
  - `summary.py`: Kaksikielinen (FI/EN) 1-pohjainen tilayhteenveto UI:lle ja kielimallille.
- **Agent & MCP (`agent/`, `mcp_server/`):**
  - `agent/tools.py`: Keskitetty työkalurekisteri (`OLLAMA_TOOLS`) ja `execute_tool`-suoritin yhdenmukaisilla 1-pohjaisilla indekseillä.
  - `agent/llm_agent.py`: Paikallinen Ollama API (`/api/chat`) tool calling -silmukalla, monivuoroisella keskusteluhistorialla ja ympäristömuuttujilla (`CUTSMITH_MODEL`, `OLLAMA_HOST`, `CUTSMITH_KEEP_ALIVE`).
  - `agent/vision.py`: Paikallinen VLM-kohdistus (`CUTSMITH_VLM_MODEL`, `CUTSMITH_VLM_KEEP_ALIVE="0"`), joka paikantaa kohteet still-ruuduista normalisoituihin `(0..1, 0..1)`-koordinaatteihin.
  - `mcp_server/server.py`: Generoi MCP-työkalut automaattisesti `agent.tools.OLLAMA_TOOLS` -rekisteristä ilman duplikaatiota.
- **View (`web/`):**
  - `web/api.py`: FastAPI-palvelin striimaavalla tiedostolatauksella, HTTP Range -mediatarjoilulla (`is_relative_to`-polkusuojaus) ja staattisten tiedostojen `no-cache`-validointiväliohjelmistolla.
  - `web/static/`: Modulaarinen tumma HTML/CSS/ES-moduulikäyttöliittymä (`index.html`, `style.css`, `i18n.js`, `state.js`, `api.js`, `scrubber.js`, `app.js`): HTML5 `<video>`-soitin, kaksiraitainen Aikajana (video + erotettu ääniraita samassa aikaskaalassa, yhteinen playhead ja `Shift+raahaus`-aluevalinta), klippinauha siirtymävalitsimineen, ääniklippien pikatoiminnot, prompt-chat, Undo/Redo sekä Export Final.

## 3. Datamalli (`model/timeline.py`)
- `VideoSource`: `id`, `path`, `duration`, `width`, `height`, `fps`, `has_audio`
- `ZoomEffect`: `type="zoom"`, `start_sec`, `duration`, `target_x` (0–1), `target_y` (0–1), `zoom_factor` (1.1–4.0)
- `TextEffect`: `type="text"`, `text`, `start_sec`, `duration`, `position` (`bottom|top|center`), `font_size`, `color`, `font_style` (`clean|cinema|mono|impact`)
- `BlurEffect`: `type="blur"`, `start_sec`, `end_sec`, `max_sigma`, `fade_sec`
- `Clip`: `id`, `source_id`, `in_sec`, `out_sec`, `speed`, `effects: list[ZoomEffect | TextEffect | BlurEffect]`
- `Transition`: `after_clip_index`, `type` (`fadeblack|fade|wipeleft|wiperight`), `duration`
- `BackgroundMusic`: `path`, `bgm_volume`, `sfx_volume`, `fade_out_sec`
- `AudioTrack`: `id`, `path`, `duration`, `origin` (`extracted|imported`), `source_video_id`
- `AudioClip`: `id`, `source_path`, `in_point`, `out_point`, `timeline_start`, `volume`, `fade_in_sec`, `fade_out_sec`
- `Timeline`: `sources`, `clips`, `transitions`, `bgm`, `mute_video_audio`, `audio_tracks`, `active_audio_track_id`, `active_audio_start_sec`, `audio_clips`, `fade_in_sec`, `fade_out_sec`, `fade_audio`, `output_fps`

## 4. Controller-, Agent- ja MCP-työkalut (1-pohjaiset indeksit ulkoisessa rajapinnassa)
1. **Perusleikkaus:** `get_state`, `add_clip`, `trim_clip`, `split_clip`, `remove_clip`, `move_clip`, `cut_timeline_range`
2. **Siirtymät ja tehosteet:** `set_transition`, `remove_transition`, `apply_crossfade_to_selection`, `add_zoom`, `add_text`, `add_blur_to_selection`, `set_timeline_fades`
3. **Ääniraidat ja erillinen ääniaikajana (J/L-cut):** `set_music`, `remove_video_audio`, `attach_audio_track`, `detach_audio_to_timeline`, `split_audio_clip`, `cut_audio_range`, `move_audio_clip`, `set_audio_clip_volume`
4. **Historia ja projektinhallinta:** `undo`, `redo`, `reset_project`

## 5. Toteutusvaiheet ja tilanne
- **Vaihe 1 (Core & Compiler):** Valmis (`model/`, `compiler/`, `controller/` + kattava `pytest`-testipaketti).
- **Vaihe 2 (Web UI & API):** Valmis (`web/api.py`, kaksikielinen FI/EN modulaarinen Web UI, kaksiraitainen Aikajana, aluevalinta, still-scrubbaus).
- **Vaihe 3 (Paikallinen Qwen + MCP):** Valmis (`agent/llm_agent.py`, keskitetty `agent/tools.py`, dynaamisesti generoitu `mcp_server/server.py`, monivuoroinen keskusteluhistoria).
- **Vaihe 4 (Visuaalinen kohdistus – VLM):** Valmis (`agent/vision.py` + `locate_object_in_frame`-työkalu agentissa ja MCP:ssä, oletusmalli `qwen2.5vl:3b`, `keep_alive="0"`).
