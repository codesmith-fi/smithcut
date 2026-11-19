# CutSmith

**CutSmith** is an AI-assisted, non-destructive video editor controlled via natural language directly in your browser. Editing commands—such as cuts, crossfades, blur transitions, and independent audio track management—compile directly into a single-pass FFmpeg `filter_complex` pipeline built on a strict Model-View-Controller (MVC) architecture.

Author: Erno Pakarinen / CODESMITH

Open source - Free - MIT LICENSE

## Features

- **Natural Language Editing:** Cut scenes, split clips at timestamps or the playhead marker, apply transitions, and manage audio tracks in English or Finnish.
- **Non-Destructive Timeline:** All edits are persisted in a lightweight JSON project state (`workspace/project_state.json`) without modifying original source media files.
- **Precision Range Selection:** Highlight exact ranges directly on the timeline (`Shift + drag`) to target cuts, blur transitions, or crossfades across clip boundaries.
- **Transitions & Effects:** Seamless boundary crossfades (`xfade` / `acrossfade`) and stepped focus-in/focus-out blur transitions (`gblur`).
- **Independent Audio Track Control:** Detach or mute the original video audio track, edit video cuts and crossfades freely without audio glitches, and attach a continuous audio track across the entire timeline.
- **Separate Audio Timeline:** Detach video audio onto its own `audio_clips` lane so picture and sound can be cut, moved, and leveled independently (J-cuts / L-cuts).
- **Timeline Fade In / Fade Out:** Apply whole-timeline fades from/to black on both video and audio (e.g. *"Tee videon alkuun 5 sekunnin fade in ja videon loppuun sama 5 sekunnin fade out"*).

---

## Prerequisites

Before installing, ensure the following are available on your system:

1. **Python 3.11+** (3.11 or 3.12 recommended)
2. **FFmpeg & FFprobe** installed and available in your system `PATH`
3. **A local LLM runtime (e.g., Ollama)** or an OpenAI-compatible API endpoint

### System Dependencies

**Linux (Ubuntu / Debian / Linux Mint):**
```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv python3-pip
```

**macOS (Homebrew):**
```bash
brew install ffmpeg python
```

**Windows (winget):**
```powershell
winget install Gyan.FFmpeg Python.Python.3.12
```

### Local LLM Setup (Ollama)

CutSmith is tested with the local **Qwen3-8B** model:

```bash
# Install Ollama (https://ollama.com) and pull the model:
ollama pull qwen3:8b
```

---

## Installation

1. **Clone the repository and enter the directory:**
   ```bash
   git clone https://github.com/codesmith-fi/smithcut.git
   cd smithcut
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate      # Linux / macOS
   # .venv\Scripts\activate       # Windows
   ```

3. **Install Python dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configure environment variables (optional):**
   If `.env.example` is present, copy it and adjust the model endpoint or name if needed:
   ```bash
   cp .env.example .env
   ```

---

## Running CutSmith

Start the server using the launch script or directly via Uvicorn:

```bash
chmod +x run.sh
./run.sh
```

Or manually:

```bash
.venv/bin/uvicorn web.api:app --reload \
  --reload-dir agent --reload-dir compiler --reload-dir controller \
  --reload-dir mcp_server --reload-dir model --reload-dir web \
  --reload-exclude 'workspace/*' --port 8000
```

Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

## Quick Start Guide

1. **Import Media:** Drag and drop a video file (e.g., `.mp4`) onto the preview or import area.
2. **Split or Cut:**
   - Click the timeline to position the playhead and type: *"Split the video into two clips here"* or *"Split the video at 2:20"*.
   - Hold **Shift** and drag across the timeline to select a range, then type: *"Cut out the selected range"*.
3. **Add a Crossfade:**
   - Highlight a range spanning the boundary between two clips and type: *"Apply a crossfade to the selected range"*, or specify exact durations: *"Take 5 seconds from the end of clip 1 and 5 seconds from the start of clip 2 and create a crossfade"*.
4. **Add a Blur Transition:**
   - Highlight a scene and type: *"Add a smooth blur transition to the start and end of the selected range"*.
5. **Manage Audio Independently:**
   - *"Remove audio track from the video"* → edit video clips and crossfades → *"Attach audio track 1 to the video"*.
6. **Separate Audio Timeline:**
   - *"Erota videosta ääni omalle raidalleen"* → cut/move audio alone (*"Siirrä ääniklippi 2 alkamaan kohdasta 0:08"*, *"Laske ääniraidan voimakkuus puoleen"*).
7. **Timeline Fade In / Out:**
   - *"Tee videon alkuun 5 sekunnin fade in ja videon loppuun sama 5 sekunnin fade out"* or *"Lisää 3 sekunnin loppuhäivytys"*.

---

## Running Tests

Run the unit and FFmpeg integration test suite with `pytest`:

```bash
.venv/bin/pytest
```

---

## Architecture

```text
cutsmith/
├── model/         # Data models (Timeline, Clip, Transition, BlurEffect, AudioTrack)
├── compiler/      # FFmpeg filter_complex compiler and preview renderer
├── controller/    # EditorController, state management, and workspace/project_state.json
├── mcp_server/    # AI agent tool interfaces
├── agent/         # LLM integration and natural language command parsing
├── web/           # FastAPI backend and browser UI
└── tests/         # Automated unit and integration tests
```