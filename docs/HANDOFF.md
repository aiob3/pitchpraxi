# PitchPraxi — Handoff Document

## What is this project?
PitchPraxi is a system-wide voice-to-text tool for Linux. Press Alt+Backspace anywhere — terminal, IDE, browser — speak, press again, and the transcription appears at your cursor.

## Origin
Forked from Jejkobb/Speechfire (browser-only STT extension). Evolved into a standalone system-wide dictation tool with native C++ engine. Upstream is inactive since Jan 2026. Repo renamed from `aiob3/speechfire` to `aiob3/pitchpraxi`.

## Current State (v1.0.0, 2026-04-15)
**Fully operational.** Running as two systemd user services on Zorin OS 18.

| Component | Status | Location |
|-----------|--------|----------|
| whisper-server (C++) | Running, auto-start | /home/brito/repos/whisper.cpp/ |
| pitchpraxi-global (Python) | Running, auto-start | /home/brito/repos/speechfire/ |
| Config | Active | ~/.config/speechfire/config.json |
| History | Logging | ~/.config/speechfire/history.jsonl |
| GitHub Release | v1.0.0 published | github.com/aiob3/pitchpraxi |

## Performance
- End-to-end: **1.27s** for 10s audio
- Server: **0.8s** (whisper.cpp base + Intel MKL)
- Paste: **0.5s** (clipboard + xdotool)
- RAM: **222MB** total (188MB server + 34MB tray)

## Key Files

| File | Purpose |
|------|---------|
| `pitchpraxi-global.py` | Main entry point: tray, hotkey, recording, paste |
| `speechfire-global.py` | Legacy entry (systemd still references this) |
| `server.py` | Python multi-engine fallback (not used in production) |
| `docs/PRD.md` | Product requirements |
| `docs/BLUEPRINT.md` | Technical architecture |
| `docs/DEPLOYMENT.md` | Installation and setup guide |
| `extension-chrome/` | Chrome extension (legacy, functional) |
| `extension-firefox/` | Firefox extension (legacy, functional) |

## Critical Knowledge

### Hotkey mapping (ABNT2)
The pynput library reports `Alt+\` as `"backspace"` on Brazilian ABNT2 keyboards. This is NOT the actual Backspace key — it's the physical `\` key. Config uses pynput key names, not physical labels.

### Smart paste
Terminal detection uses `xprop -id $(xdotool getactivewindow) WM_CLASS`. Do NOT use `xdotool getwindowclassname` — it doesn't exist in Ubuntu 24.04's xdotool version.

### Engine selection
Production uses whisper-server C++ binary directly (not Python). The `server.py` Python file exists as fallback with two engines (faster-whisper, qwen3-asr) but is NOT used in production systemd.

### NEVER refactor working hotkey code
The hotkey handler in pitchpraxi-global.py was broken twice during refactoring. The `_is_modifier_pressed` + `_on_key_press` with char/name matching is HOMOLOGATED. Add new features without modifying this code.

## Engines Tested (for context)

| Engine | Result | Why |
|--------|--------|-----|
| openai-whisper small | 4.9s, 2.4GB | Python overhead, slow |
| faster-whisper base INT8 | 2.8s, 546MB | Good but CTranslate2 < MKL |
| Qwen3-ASR ONNX INT8 | 5.8s, 2.9GB | Autoregressive decoder slow on CPU |
| **whisper.cpp base MKL** | **1.27s, 188MB** | **Winner — AVX-VNNI native** |

## Roadmap (v1.1+)

1. **Noise/music filter** — Add `--no-speech-thold 0.6` to server (user-configurable)
2. **Translation PT→EN** — Implemented in menu, needs `translate=true` in whisper-server
3. **Auto-correction** — Post-processing for PT-BR spelling errors
4. **Streaming** — Transcribe while speaking (whisper.cpp stream mode)
5. **Config migration** — Move from ~/.config/speechfire/ to ~/.config/pitchpraxi/

## How to Resume Work

1. Read this document + docs/BLUEPRINT.md for architecture
2. Check `systemctl --user status speechfire speechfire-global`
3. Check `~/.config/speechfire/config.json` for current settings
4. Check `journalctl --user -u speechfire-global -n 20` for recent activity
5. Read memory files at `~/.claude/projects/-home-brito/memory/project_speechfire*`
6. **NEVER modify the hotkey handler without operator approval**

## Operator Rules
- "compartilhar" / "salvar" = commit + push automatically
- Branches = homologation environments, NEVER merge to main without explicit approval
- NEVER rewrite working code — only ADD new code
- Config changes go in JSON files, never hardcoded
