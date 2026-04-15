# PitchPraxi — Product Requirements Document (PRD)

## Vision
System-wide voice-to-text dictation for Linux that works in any application with sub-2s latency, running entirely local on CPU.

## Problem
- Existing STT solutions are cloud-dependent, slow, or browser-only
- OpenAI Whisper (Python) is too slow for interactive dictation on CPU (~5s latency)
- No open-source solution provides global hotkey + system tray + smart paste on Linux

## Solution
Native C++ inference engine (whisper.cpp + Intel MKL) with Python system tray controller. Global hotkey captures audio, transcribes via local server, and pastes into the focused application automatically.

## Target User
Developers on Linux who need hands-free dictation across terminal, IDE, browser, and desktop apps.

## Requirements

### Functional
| ID | Requirement | Status |
|----|------------|--------|
| F1 | Global hotkey to start/stop recording | Done (Alt+Backspace) |
| F2 | Transcribe audio to text in Portuguese | Done (whisper.cpp base) |
| F3 | Paste text into focused application | Done (smart paste: terminal vs GUI) |
| F4 | System tray icon with visual states | Done (green/red/blue) |
| F5 | Multi-language support | Done (10 languages + auto-detect) |
| F6 | Translate speech to English | Done (toggle in tray menu) |
| F7 | Configurable hotkey via GUI | Done (tray menu dialog) |
| F8 | Persistent config | Done (~/.config/pitchpraxi/config.json) |
| F9 | Transcription history | Done (~/.config/pitchpraxi/history.jsonl) |
| F10 | Server restart from tray | Done |
| F11 | Copy logs for debugging | Done |
| F12 | Auto-start on boot | Done (systemd user services) |

### Non-Functional
| ID | Requirement | Target | Actual |
|----|------------|--------|--------|
| NF1 | End-to-end latency | <2s | 1.27s |
| NF2 | Server RAM | <500MB | 188MB |
| NF3 | Total RAM (server+tray) | <500MB | 222MB |
| NF4 | No GPU required | CPU-only | Intel MKL AVX-VNNI |
| NF5 | No cloud dependency | 100% local | 100% local |
| NF6 | No Python in inference path | Native C++ | whisper-server binary |

### Roadmap (v1.1+)
| ID | Feature | Priority |
|----|---------|----------|
| R1 | Noise/music filter (--no-speech-thold) | High |
| R2 | Auto-correction (PT-BR spelling) | Medium |
| R3 | Streaming transcription (while speaking) | Medium |
| R4 | Configurable hotkey via tray (validated ABNT2) | Low (implemented, needs testing) |

## Success Metrics
- Latency: <2s end-to-end for 10s audio clips
- Accuracy: Whisper base-level WER for Portuguese
- Uptime: systemd auto-restart on failure
- Adoption: Usable by operator (Brito) in daily workflow

## Constraints
- Intel CPU with AVX2+ (Haswell or newer)
- Linux with X11 (Wayland needs xdotool alternatives)
- ABNT2 keyboard: pynput reports keys differently (e.g., backspace for \)
