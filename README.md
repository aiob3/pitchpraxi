# PitchPraxi

System-wide voice-to-text for Linux, powered by **whisper.cpp** + **Intel MKL**.

> *Resgatando o primórdio do Pitch Perfect — da voz ao texto em qualquer aplicação.*

Originally forked from [Jejkobb/Speechfire](https://github.com/Jejkobb/Speechfire), PitchPraxi evolved into a standalone system-wide dictation tool with a native C++ inference engine, configurable hotkeys, multi-language support, and a system tray interface.

## Features

- **System-wide dictation** — Works in any application (terminal, VS Code, Cursor, browser, etc.)
- **~1.2s end-to-end latency** — whisper.cpp with Intel MKL (AVX-VNNI) on CPU
- **188MB RAM** — Lightweight native C++ server, no Python runtime overhead
- **10 languages** — Portuguese, English, Spanish, French, German, Italian, Japanese, Chinese, Korean + auto-detect
- **Translate → English** — Speak in any language, get text in English
- **Configurable hotkey** — Default: `Alt+Backspace`. Change via system tray menu
- **System tray** — Full control: language switch, translate toggle, server restart, log export, transcription history
- **Smart paste** — Detects terminal (Ctrl+Shift+V) vs GUI apps (Ctrl+V) automatically
- **Persistent config** — Settings saved in `~/.config/pitchpraxi/config.json`
- **Transcription history** — Stored in `~/.config/pitchpraxi/history.jsonl`
- **Optional noise filter** — `--no-speech-thold` to suppress music/background noise

## Architecture

```
┌─────────────────────┐     ┌──────────────────────────┐
│  pitchpraxi-global  │────▶│   whisper-server (C++)   │
│  (Python, 34MB)     │     │   whisper.cpp + MKL      │
│                     │     │   (188MB, port 5000)     │
│  • System tray      │◀────│                          │
│  • Global hotkey    │JSON │  Model: ggml-base.bin    │
│  • Mic recording    │     │  Engine: Intel MKL       │
│  • Smart paste      │     │  Threads: 8              │
└─────────────────────┘     └──────────────────────────┘
```

## Performance Benchmarks (Intel i7-1355U, CPU-only)

| Engine | 10s Audio | RAM | Status |
|--------|-----------|-----|--------|
| openai-whisper small (Python) | 4.9s | 2.4GB | Replaced |
| faster-whisper base INT8 | 2.8s | 546MB | Replaced |
| Qwen3-ASR ONNX INT8 | 5.8s | 2.9GB | Tested, slower |
| **whisper.cpp base MKL (C++)** | **1.27s** | **188MB** | **Current** |

## Prerequisites

- Linux (tested on Zorin OS 18 / Ubuntu 24.04)
- Intel CPU with AVX2 (Haswell+) — AVX-VNNI recommended (12th gen+)
- Intel oneAPI MKL (`intel-oneapi-mkl-devel`)
- Python 3.12+ (for system tray only)
- FFmpeg, xdotool, xclip, xprop

## Installation

### 1. Build whisper.cpp with Intel MKL

```bash
# Install Intel oneAPI
wget -qO - https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB | \
  sudo gpg --dearmor -o /usr/share/keyrings/intel-oneapi-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/intel-oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main" | \
  sudo tee /etc/apt/sources.list.d/intel-oneapi.list
sudo apt update && sudo apt install -y intel-oneapi-compiler-dpcpp-cpp intel-oneapi-mkl-devel cmake

# Clone and build
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
source /opt/intel/oneapi/setvars.sh
cmake -B build -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=Intel10_64lp \
  -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx
cmake --build build --config Release -j$(nproc)

# Download model
bash models/download-ggml-model.sh base
```

### 2. Install PitchPraxi

```bash
git clone https://github.com/aiob3/pitchpraxi.git
cd pitchpraxi

# System dependencies
sudo apt install -y python3.12-venv ffmpeg xdotool xclip \
  python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-appindicator3-0.1 \
  portaudio19-dev

# Python venv (with system GTK access)
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install pynput pyaudio requests
```

### 3. Set up systemd services

```bash
# Server (whisper.cpp)
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/pitchpraxi.service << 'EOF'
[Unit]
Description=PitchPraxi STT Server (whisper.cpp + Intel MKL)
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/whisper.cpp
Environment=LD_LIBRARY_PATH=/opt/intel/oneapi/mkl/latest/lib:/opt/intel/oneapi/compiler/latest/lib
ExecStart=/path/to/whisper.cpp/build/bin/whisper-server -m models/ggml-base.bin -l pt --port 5000 -t 8 --no-timestamps
Restart=on-failure

[Install]
WantedBy=default.target
EOF

# Global (tray + hotkey)
cat > ~/.config/systemd/user/pitchpraxi-global.service << 'EOF'
[Unit]
Description=PitchPraxi Global — System-wide voice-to-text
After=pitchpraxi.service
Requires=pitchpraxi.service

[Service]
Type=simple
WorkingDirectory=/path/to/pitchpraxi
Environment=DISPLAY=:0
ExecStart=/path/to/pitchpraxi/.venv/bin/python pitchpraxi-global.py
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable pitchpraxi pitchpraxi-global
systemctl --user start pitchpraxi pitchpraxi-global
```

## Usage

- **`Alt+Backspace`** — Start/stop recording (configurable via tray menu)
- **System tray icon** — Right-click for full menu:
  - Language quick-switch (10 languages)
  - Translate → English toggle
  - Change Hotkey
  - Restart Server
  - Copy Recent Logs
  - Transcription History

### Optional: Noise/Music Filter

Add `--no-speech-thold 0.6` to the whisper-server command in the systemd service to suppress non-speech segments:

```bash
ExecStart=... --no-timestamps --no-speech-thold 0.6
```

Higher values (0.8+) are more aggressive. Adjust based on your environment.

## Configuration

Settings stored in `~/.config/pitchpraxi/config.json`:

```json
{
  "server_url": "http://127.0.0.1:5000",
  "language": "pt",
  "hotkey_modifier": "alt",
  "hotkey_key": "backspace",
  "translate_to_en": false
}
```

## History

Originally **Speechfire** by [Jejkobb](https://github.com/Jejkobb/Speechfire) — a Firefox/Chrome extension for offline STT. Forked and evolved by [aiob3](https://github.com/aiob3) into **PitchPraxi**: a system-wide dictation tool with native C++ engine, 6x faster inference, 13x less memory, and full system tray integration.

## License

MIT — see [LICENSE](./LICENSE) for details.
