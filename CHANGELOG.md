# Changelog

All notable changes to Karen Assistant are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing pending.

## [1.0.0] — 2026-11-10

First source-available release. Windows-only.

### Added

- **Voice pipeline**: Kokoro TTS (offline), faster-whisper STT (offline),
  barge-in interruption, animated orb reactive to audio level.
- **Three brain modes**: Cloud (Groq / Cerebras / Gemini / OpenRouter /
  Mistral), Local (llama.cpp / Ollama / LM Studio), Hybrid with automatic
  fallback between local and cloud.
- **Local lanes**: dedicated router (LFM2.5-230M) and tool (LFM2.5-1.2B-Nova)
  models for intent classification and function calling.
- **Desktop agent**: opens apps and websites, clicks UI controls via
  Windows UIAutomation, types text, presses keys. **No mouse movement.**
- **Screen vision**: Qwen2.5-VL reads the screen to answer questions,
  summarize text, or name videos.
- **Screen glance**: optional background screen-context refresh with a
  sensitive-window deny list (password managers, private browsing).
- **Scheduled tasks**: `remind me to X in 20 minutes`,
  `at 5pm open spotify`, `every day at 9am take a screenshot`.
- **Fast-path router**: `type X`, `open X`, `press X`, `screenshot`,
  `mute`, `next track`, `volume N`, `lock screen`, `play`, `pause` — all
  fire without an LLM turn.
- **Dual-vote routing**: router model and regex must both be wrong before
  a command lands in the wrong lane.
- **Anti-loop enforcement**: two identical tool calls block, four
  consecutive failures abort.
- **OLED deep-black UI**: dark purple theme, animated orb, live context
  panel, cheerful persona with emotional voice variation.
- **Console-free EXE**: `build.spec` produces a GUI-only binary via
  PyInstaller with bundled Python runtime.

### Known limitations

- Windows 10/11 only (UIAutomation is Windows-specific).
- Local models require an NVIDIA GPU (5 GB+ VRAM) for good latency.
- Cloud mode requires a Groq (or other provider) API key.
- Kokoro must be downloaded via the GUI on first use (~340 MB).

[Unreleased]: https://github.com/USERNAME/karen-assistant/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/USERNAME/karen-assistant/releases/tag/v1.0.0
