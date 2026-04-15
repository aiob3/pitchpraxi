# PitchPraxi — Claude Code Instructions

## Project Overview
System-wide voice-to-text for Linux. whisper.cpp (C++) server + Python system tray.
Repo: github.com/aiob3/pitchpraxi (renamed from speechfire)

## Architecture
- **Server**: whisper.cpp binary with Intel MKL at /home/brito/repos/whisper.cpp/
- **Client**: pitchpraxi-global.py (Python, GTK3 tray, pynput hotkey, PyAudio mic)
- **Config**: ~/.config/speechfire/config.json
- **Services**: speechfire.service + speechfire-global.service (systemd user)

## Rules

### DO NOT modify these (homologated):
- The hotkey handler (_is_modifier_pressed, _on_key_press, _on_key_release)
- The paste logic (_paste_text, _is_terminal_focused, _get_focused_wm_class)
- The recording logic (_start_recording, _stop_recording, _audio_callback)
- The transcribe logic (_transcribe)

### When adding features:
- Create NEW methods. Call them from existing flow without changing existing code.
- Test hotkey (Alt+Backspace) still works after every change.
- Use config.json for user-facing settings, never hardcode.

### ABNT2 keyboard note:
pynput reports Alt+\ as key name "backspace" on Brazilian keyboards.
Do NOT change this mapping without testing on the actual hardware.

## Key Commands
```bash
# Status
systemctl --user status speechfire speechfire-global

# Restart
systemctl --user restart speechfire speechfire-global

# Logs
journalctl --user -u speechfire-global -f

# Test server
curl -s http://127.0.0.1:5000/inference -F "file=@test.wav" -F "response_format=json" -F "language=pt"
```

## Docs
- docs/PRD.md — Requirements and roadmap
- docs/BLUEPRINT.md — Architecture and design decisions
- docs/DEPLOYMENT.md — Installation guide
- docs/HANDOFF.md — Context for new agents/developers
