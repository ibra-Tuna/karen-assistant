<div align="center">

# Karen Assistant

**A local-first Windows voice assistant that controls your PC — without the mouse.**

[![License: MIT](https://img.shields.io/badge/License-MIT-9d4edd.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-9d4edd)](pyproject.toml)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-9d4edd)](#requirements)
[![Status](https://img.shields.io/badge/Status-v1.0.0-9d4edd)](#)

[Overview](#what-is-karen) · [Features](#features) · [Requirements](#requirements) · [Quick start](#quick-start) · [Commands](#what-you-can-say) · [Architecture](#architecture)

</div>

<p align="center">
  <img src="assets/screenshot.png" alt="Karen Assistant GUI — dark purple orb, chat history, and live context panel" width="880">
</p>

---

## What is Karen?

Karen is a desktop AI assistant that you **talk to** (or type to) on
Windows. She speaks back with an offline neural voice, understands what
you say with offline speech recognition, and can open apps, type text,
press keys, click UI elements, and schedule tasks — all through
**Windows UI Automation and keyboard input**.

She has **no mouse access by design**. Every interaction is a real
control (button, field, menu) found by name or identifier, or a keyboard
shortcut. This makes her fast, deterministic, and testable — and means
she can't accidentally drag your files around or click the wrong pixel.

She's also context-aware. With **Screen glance** enabled, she periodically
reads your screen so you can ask *"what's this?"* or *"summarize that"*
without describing anything first.

---

## Features

| Feature | What it does |
|---|---|
| 🎙️ **Voice or text** | Kokoro TTS (offline) and faster-whisper STT (offline). Talk, type, or both. |
| 🧠 **Three brain modes** | **Cloud** (Groq / Cerebras / Gemini / OpenRouter / Mistral), **Local** (llama.cpp / Ollama / LM Studio), or **Hybrid** with automatic fallback. |
| 🖥️ **Real desktop control** | Opens apps and websites, clicks UI controls, types text, presses keys. Zero mouse movement. |
| 👁️ **Screen vision** | Qwen2.5-VL reads your screen to answer questions, summarize text, or name videos. |
| ⚡ **Fast-path commands** | `type hello`, `press ctrl+s`, `screenshot`, `mute`, `lock screen`, `next track`, `volume 50` — instant, no LLM turn. |
| ⏰ **Scheduled tasks** | *"remind me to stretch in 20 minutes"*, *"at 5pm open Spotify"*, *"every day at 9am take a screenshot"*. |
| 🎨 **OLED deep-black UI** | Dark purple theme, animated orb, live context panel, and a cheerful persona you can swap out. |
| 🔒 **No telemetry** | Nothing leaves your PC except to the LLM you configure, or to HuggingFace/GitHub for one-time model downloads. |

---

## Requirements

- **Windows 10 or 11 (64-bit).** The desktop-control layer uses Windows
  UI Automation — macOS and Linux are not supported.
- **Python 3.10 – 3.12 (64-bit).** Python 3.13 is not yet supported due
  to `onnxruntime` wheel availability.
- **8 GB RAM** minimum. **16 GB** recommended for local models.
- **NVIDIA GPU with 5+ GB VRAM** for fast local inference.
  Optional — the app runs on CPU, but local chat will be slow.
- **A microphone** for voice input. Not required — you can type instead.

---

## Quick start

### 1. Clone and install

```powershell
git clone https://github.com/USERNAME/karen-assistant.git
cd karen-assistant

python -m venv .venv
.venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

**GPU acceleration** (optional, recommended for local models):

```powershell
pip install onnxruntime-gpu
```

### 2. Run

```powershell
python karen.py
```

The GUI opens. On first launch you'll see:

> *"Hi there! Karen online and ready."*
>
> *"No API key and no local model. Open Settings → AI."*

### 3. Configure

Open **⚙ Settings** (top right) and pick one:

**Cloud — easiest, no GPU needed**

1. Settings → AI → Brain mode = **Cloud**
2. Paste a free Groq key from [console.groq.com/keys](https://console.groq.com/keys)
3. Settings → Models → download **Kokoro** (~340 MB)
4. Settings → Voice → click **Test voice**

**Local — fully offline**

1. Settings → AI → Brain mode = **Local**
2. Settings → Models → download **Chat**, **Tool**, and **Router** models
3. Settings → Models → download **Kokoro** (~340 MB) and a **Whisper** STT model
4. Settings → Voice → click **Test voice**

**Hybrid — best of both**

Both of the above. Local for cheap turns, cloud for hard ones — automatic.

### 4. Talk

Press the mic button or the **space bar**. Say *"open notepad"*, or type
it in the box. Karen responds.

---

## What you can say

Karen has a fast-path router that fires these commands in under 100 ms —
no LLM turn, no token spend:

| Command | What happens |
|---|---|
| `hi` / `hello` | Karen greets you in her voice |
| `open notepad` | App launches (any app on your PC) |
| `open youtube.com` | Browser opens the site |
| `close notepad` | Notepad closes |
| `focus chrome` | Brings Chrome to the front |
| `google best pizza near me` | Searches Google in your browser |
| `type hello world` | Types into whatever has focus |
| `press ctrl+s` | Sends the key chord |
| `screenshot` | Captures the screen to `~/Pictures/Karen/` |
| `mute` / `unmute` | Toggles system mute |
| `volume 50` | Sets volume to 50% |
| `next track` / `previous track` | Skips media playback |
| `play` / `pause` | Toggles media |
| `lock the screen` | Locks Windows |

The **agent** handles multi-step and screen-reading commands. These take
an LLM turn but are much smarter:

| Command | What happens |
|---|---|
| `what's on my screen` | Vision model describes the current window |
| `summarize this page` | Reads text and summarizes |
| `open notepad and type hello` | Launches, focuses, types — chained |
| `in notepad type hello into the text area` | Targets a specific field |
| `reply to that message yes` | Reads the conversation, types, sends |
| `open chrome and search for cats` | Multi-step with a browser |
| `remind me to drink water in 30 minutes` | Schedules a reminder |
| `at 5pm open spotify` | Schedules a launch |
| `every day at 9am take a screenshot` | Recurring task |
| `list my tasks` | Shows all scheduled tasks |
| `cancel all tasks` | Clears the schedule |

---

## Brain modes explained

| Mode | Chat | Local lanes used | Best for |
|---|---|---|---|
| **Cloud** | Hosted LLM (Groq by default) | Optional tool + router lanes | Fast, high-quality chat with no local VRAM |
| **Local** | llama.cpp, Ollama, or LM Studio | Chat + tool + router | Full privacy, offline use, requires GPU |
| **Hybrid** | Local for simple turns, cloud for hard ones | Chat + tool + router | Mixed workloads, opportunistic |

In **Cloud** and **Hybrid** modes, the local **tool** and **router** lanes
still run if configured — they classify intent and route function calls
before hitting the cloud. That saves tokens and latency on every action
command.

---

## Architecture

Four source files:

```
karen-assistant/
├── karen.py        ← GUI, voice pipeline, brain, config, model management
├── agent.py        ← Desktop agent — plans and executes tool calls step by step
├── toolkit.py      ← Tool surface — UIAutomation, files, shell, web, media, OCR, tasks
└── tasks.py        ← Scheduled task parser and scheduler
```

### How it fits together

```
User speaks or types
        │
        ▼
  ┌──────────────┐         ┌─────────────────┐
  │  karen.py    │ ──plan─→│  agent.py       │───tool call──┐
  │  (brain)     │←─result─│  (agent loop)   │              │
  └──────┬───────┘         └─────────────────┘              ▼
         │              Anti-loop:                  ┌───────────────┐
         │              · 2 identical calls block   │  toolkit.py   │
         │              · A→B→A→B stops             │  (68 tools)   │
         ▼              · 4 consecutive fails stop  └───────────────┘
  ┌──────────────┐
  │  Kokoro TTS  │  ← Response spoken back
  └──────────────┘
```

### Design decisions

- **No mouse.** Every interaction is a real control (found by UIAutomation
  name or identifier) or a keyboard shortcut. This makes the agent
  deterministic, testable, and impossible to misclick.
- **Fast-path before LLM.** Common commands match a regex in `agent.py`
  and fire immediately — no LLM latency.
- **Dual-vote routing.** The router model and the regex must both be wrong
  before a command lands in the wrong lane.
- **Anti-loop enforcement.** Two identical tool calls in a row block; four
  consecutive failures abort. No infinite retry spirals.
- **No telemetry.** Config and memory live on your machine. Nothing is
  uploaded for training.

---

## Building an EXE

If you want a single-folder distribution that doesn't need Python:

```powershell
pip install pyinstaller
pyinstaller --clean --noconfirm build.spec
```

Produces `dist/Karen/Karen.exe` with **no console window**. Distribute
the entire `dist/Karen/` folder — the `_internal/` subfolder contains the
Python runtime and all native libraries.

On first launch, place your Kokoro models next to `Karen.exe`:

```powershell
Copy-Item kokoro-v1.0.onnx   dist\Karen\
Copy-Item voices-v1.0.bin    dist\Karen\
```

The rest (chat/tool/router models, Whisper, vision) can be downloaded
through the GUI on the target machine.

---

## Configuration

All settings live in `karen_config.json` next to the executable (or in
`%APPDATA%\Karen\` if the install folder is read-only). The app manages
this file — you rarely need to edit it by hand.

| Settings tab | What you'll find |
|---|---|
| **AI** | Brain mode, provider + key, local model path, local lanes, personality |
| **Voice** | Kokoro voice, pitch, effect, mic, Whisper device, speech detection |
| **PC control** | Enable PC control, screen reading, vision model, screen glance, agent behavior |
| **Tasks** | Scheduled task list, natural-language task input |
| **Models** | One-click downloads: Kokoro, Whisper, chat/tool/router, vision LLM |
| **Interface** | Theme, accent color, orb palette, animation toggle |

---

## Known limitations

- **Windows only.** The desktop control layer relies on Windows UI
  Automation. macOS and Linux are out of scope.
- **No mouse control by design.** If a target app has no accessible
  controls, the agent can't reach it. Prefer apps that expose UIAutomation
  names.
- **Local models need GPU.** A 4B chat model on CPU runs at ~1 token/s.
  A 6 GB GPU runs it at 30–60 tokens/s.
- **Vision is optional.** Downloading the vision model (~2.5 GB) is only
  needed if you want screen understanding.
- **Elevated windows.** Karen can't type into apps running as
  administrator unless Karen itself is launched as administrator.
- **No auto-update.** This is a source project. Pull to update.

---

## Contributing

Bug reports and feature requests welcome via GitHub Issues. For PRs,
open one against `main` and describe the change in the description.

Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before your first PR.

---

## Security

Karen can read your screen and control apps — that's the point. See
[`SECURITY.md`](SECURITY.md) for what's enabled, what's disabled, and how
to report a vulnerability privately.

---

## Credits

Karen stands on the shoulders of open-source work:

- [Kokoro](https://github.com/hexgrad/kokoro) — offline neural TTS
- [Kokoro-ONNX](https://github.com/thewh1teagle/kokoro-onnx) — ONNX runtime for Kokoro
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — offline speech recognition
- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) — local GGUF inference
- [PySide6](https://pypi.org/project/PySide6/) — cross-platform Qt bindings
- [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) — Windows UIAutomation bindings
- [Qwen 2.5](https://huggingface.co/Qwen) — chat and vision models
- [LFM 2.5](https://huggingface.co/LiquidAI) — router and tool-calling models

Each dependency retains its own license.

---

## License

MIT — see [`LICENSE`](LICENSE).
