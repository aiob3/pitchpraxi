# PitchPraxi — Technical Blueprint

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    PitchPraxi System                         │
├──────────────────────┬───────────────────────────────────────┤
│  pitchpraxi-global   │   whisper-server (C++)               │
│  (Python 3.12)       │   whisper.cpp + Intel MKL            │
│                      │                                       │
│  ┌─ pynput ─────┐   │   ┌─ Flask-like HTTP ──────────┐     │
│  │ Global hotkey │   │   │ POST /inference            │     │
│  │ Alt+Backspace │   │   │   file: audio.wav          │     │
│  └──────┬────────┘   │   │   language: pt             │     │
│         │            │   │   translate: true/false     │     │
│  ┌──────▼────────┐   │   │                            │     │
│  │ PyAudio       │   │   │ Response:                  │     │
│  │ Mic recording │───┼──▶│   {"text": "..."}          │     │
│  │ 16kHz mono    │   │   └────────────────────────────┘     │
│  └──────┬────────┘   │                                       │
│         │            │   Engine: ggml-base.bin (147MB)       │
│  ┌──────▼────────┐   │   Threads: 8 (MKL BLAS)             │
│  │ requests POST │───┤   AVX-VNNI: enabled                  │
│  └──────┬────────┘   │   Port: 5000                         │
│         │            │                                       │
│  ┌──────▼────────┐   │                                       │
│  │ Smart Paste   │   │                                       │
│  │ xprop→detect  │   │                                       │
│  │ terminal: C-S-V│  │                                       │
│  │ GUI: C-V      │   │                                       │
│  └──────┬────────┘   │                                       │
│         │            │                                       │
│  ┌──────▼────────┐   │                                       │
│  │ GTK3 Tray     │   │                                       │
│  │ AppIndicator3 │   │                                       │
│  │ Menu + States │   │                                       │
│  └───────────────┘   │                                       │
├──────────────────────┴───────────────────────────────────────┤
│  systemd user services (auto-start on login)                 │
│  speechfire.service → whisper-server                         │
│  speechfire-global.service → pitchpraxi-global.py            │
└──────────────────────────────────────────────────────────────┘
```

## Components

### 1. whisper-server (C++ binary)
- **Source**: github.com/ggml-org/whisper.cpp (compiled locally)
- **Location**: /home/brito/repos/whisper.cpp/build/bin/whisper-server
- **Model**: /home/brito/repos/whisper.cpp/models/ggml-base.bin (147MB)
- **Build flags**: GGML_BLAS=ON, GGML_BLAS_VENDOR=Intel10_64lp, icx/icpx compilers
- **API**: HTTP POST /inference (multipart form: file + language + translate)
- **Response**: JSON {"text": "transcribed text"}

### 2. pitchpraxi-global.py (Python 3.12)
- **Location**: /home/brito/repos/speechfire/pitchpraxi-global.py
- **Dependencies**: pynput, pyaudio, requests, gi (GTK3, AppIndicator3)
- **Venv**: /home/brito/repos/speechfire/.venv (--system-site-packages for GTK)
- **Config**: ~/.config/speechfire/config.json (will migrate to pitchpraxi/)
- **History**: ~/.config/speechfire/history.jsonl

### 3. server.py (Python, multi-engine fallback)
- **Location**: /home/brito/repos/speechfire/server.py
- **Engines**: "whisper" (faster-whisper) or "qwen3" (Qwen3-ASR ONNX)
- **Status**: Fallback only. Production uses whisper-server C++ directly.

### 4. Browser Extensions (legacy, from Speechfire)
- Chrome: extension-chrome/ (Manifest V3)
- Firefox: extension-firefox/ + firefox-extension.xpi
- Status: Functional but superseded by global hotkey system

## Data Flow

```
User presses Alt+Backspace
  → pynput detects key combo
  → PyAudio opens mic stream (16kHz, mono, PCM16)

User speaks...

User presses Alt+Backspace again
  → PyAudio stops stream
  → WAV written to tempfile (~0.002s)
  → HTTP POST to localhost:5000/inference (~0.8s)
  → whisper.cpp processes audio (MKL BLAS, 8 threads)
  → JSON response with transcription text

  → xprop detects focused window WM_CLASS
  → If terminal: xclip + xdotool Ctrl+Shift+V
  → If GUI app: xclip + xdotool Ctrl+V
  → Tray icon: green (ready)
  → History entry appended to JSONL
```

## Configuration Schema

```json
{
  "server_url": "http://127.0.0.1:5000",
  "language": "pt",
  "hotkey_modifier": "alt",
  "hotkey_key": "backspace",
  "translate_to_en": false
}
```

## Key Design Decisions

| Decision | Why |
|----------|-----|
| whisper.cpp over Python Whisper | 6x faster, 13x less RAM on CPU |
| Intel MKL over generic BLAS | AVX-VNNI native INT8 on i7-1355U |
| pynput over dbus/keybinder | Works without root, cross-DE |
| xdotool+xclip over pyperclip | Terminal Ctrl+Shift+V detection |
| xprop over xdotool getwindowclassname | Latter doesn't exist in Ubuntu 24.04 xdotool |
| JSONL over SQLite for history | Append-only, no schema, human-readable |
| systemd user over cron/autostart | Dependency chain, auto-restart, journalctl |
| Config JSON over env vars | User can edit without touching systemd |

## Engines Tested

| Engine | Latency (10s audio) | RAM | Verdict |
|--------|---------------------|-----|---------|
| openai-whisper small | 4.9s | 2.4GB | Too slow |
| faster-whisper base INT8 | 2.8s | 546MB | Good fallback |
| Qwen3-ASR ONNX INT8 | 5.8s | 2.9GB | Decoder bottleneck on CPU |
| **whisper.cpp base MKL** | **1.27s** | **188MB** | **Production** |
