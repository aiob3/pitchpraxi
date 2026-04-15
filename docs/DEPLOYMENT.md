# PitchPraxi — Deployment Guide

## Prerequisites

| Component | Version | Install |
|-----------|---------|---------|
| Linux | Ubuntu 24.04+ / Zorin 18+ | — |
| Intel CPU | AVX2+ (Haswell+), AVX-VNNI ideal (12th gen+) | — |
| Python | 3.12+ | `sudo apt install python3.12-venv` |
| FFmpeg | 6.x | `sudo apt install ffmpeg` |
| Intel oneAPI | MKL + DPCPP compiler | See below |
| System tools | xdotool, xclip, xprop | `sudo apt install xdotool xclip x11-utils` |
| GTK3 | gi, AppIndicator3 | `sudo apt install python3-gi gir1.2-appindicator3-0.1` |
| PortAudio | 19+ | `sudo apt install portaudio19-dev` |

## Step 1: Install Intel oneAPI (MKL)

```bash
wget -qO - https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB | \
  sudo gpg --dearmor -o /usr/share/keyrings/intel-oneapi-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/intel-oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main" | \
  sudo tee /etc/apt/sources.list.d/intel-oneapi.list
sudo apt update
sudo apt install -y intel-oneapi-compiler-dpcpp-cpp intel-oneapi-mkl-devel cmake
```

## Step 2: Build whisper.cpp

```bash
cd ~/repos
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
source /opt/intel/oneapi/setvars.sh
cmake -B build -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=Intel10_64lp \
  -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx
cmake --build build --config Release -j$(nproc)
bash models/download-ggml-model.sh base
```

Verify: `./build/bin/whisper-cli -m models/ggml-base.bin -f samples/jfk.wav -l en`

## Step 3: Install PitchPraxi

```bash
cd ~/repos
git clone git@github.com:aiob3/pitchpraxi.git
cd pitchpraxi
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install pynput pyaudio requests
```

## Step 4: Configure systemd services

### Server service
```bash
cat > ~/.config/systemd/user/speechfire.service << 'EOF'
[Unit]
Description=PitchPraxi STT Server (whisper.cpp + Intel MKL)
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/USER/repos/whisper.cpp
Environment=LD_LIBRARY_PATH=/opt/intel/oneapi/mkl/latest/lib:/opt/intel/oneapi/compiler/latest/lib
Environment=MKL_NUM_THREADS=8
ExecStart=/home/USER/repos/whisper.cpp/build/bin/whisper-server -m /home/USER/repos/whisper.cpp/models/ggml-base.bin -l pt --port 5000 -t 8 --no-timestamps
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
```

### Global service (tray + hotkey)
```bash
cat > ~/.config/systemd/user/speechfire-global.service << 'EOF'
[Unit]
Description=PitchPraxi Global — System-wide voice-to-text
After=speechfire.service
Requires=speechfire.service

[Service]
Type=simple
WorkingDirectory=/home/USER/repos/pitchpraxi
Environment=DISPLAY=:0
ExecStart=/home/USER/repos/pitchpraxi/.venv/bin/python pitchpraxi-global.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
```

Replace `USER` with your username, then:
```bash
systemctl --user daemon-reload
systemctl --user enable speechfire speechfire-global
systemctl --user start speechfire speechfire-global
```

## Step 5: Configure hotkey

Edit `~/.config/pitchpraxi/config.json` (created on first run):
```json
{
  "server_url": "http://127.0.0.1:5000",
  "language": "pt",
  "hotkey_modifier": "alt",
  "hotkey_key": "backspace",
  "translate_to_en": false
}
```

Note: Run pynput key capture to find your key name:
```python
from pynput import keyboard
def on_press(key): print(f'name={getattr(key,"name",None)} char={getattr(key,"char",None)}')
with keyboard.Listener(on_press=on_press) as l: l.join()
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Server not starting | `journalctl --user -u speechfire.service -n 20` |
| Hotkey not working | Check config.json hotkey_key matches pynput output |
| No paste in terminal | Verify xdotool and xclip installed |
| ALSA/Jack warnings | Harmless — PipeWire/PulseAudio handles audio |
| "No device" error | Check `pactl list sources short` for mic |
| High latency | Increase threads (`-t`), try model tiny |

## Commands

```bash
# Status
systemctl --user status speechfire speechfire-global

# Restart
systemctl --user restart speechfire speechfire-global

# Logs (live)
journalctl --user -u speechfire-global -f

# Stop
systemctl --user stop speechfire speechfire-global
```

## Optional: Noise filter

Add to whisper-server ExecStart line:
```
--no-speech-thold 0.6
```
