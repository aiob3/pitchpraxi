#!/usr/bin/env python3
"""
Speechfire Global — System-wide voice-to-text for Linux.
Captures Alt+A globally, records microphone, sends to local Whisper server,
and pastes transcription into the focused application via xdotool.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

import os
import sys
import time
import wave
import tempfile
import threading
import subprocess
import logging
import signal
import requests
import pyaudio
from pynput import keyboard

# --- Configuration ---
SERVER_URL = os.environ.get('SPEECHFIRE_URL', 'http://127.0.0.1:5000')
LANGUAGE = os.environ.get('SPEECHFIRE_LANG', 'Portuguese')
HOTKEY = keyboard.Key.f9  # Fallback; Alt+A handled via combo detection
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 1024
FORMAT = pyaudio.paInt16

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger('speechfire-global')

# --- Paths ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(PROJECT_ROOT, 'extension-firefox', 'icon')


class SpeechfireGlobal:
    def __init__(self):
        self.recording = False
        self.audio_frames = []
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.alt_pressed = False

        # Build tray icon
        self.indicator = AppIndicator3.Indicator.new(
            'speechfire-global',
            'icon',
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_icon_theme_path(ICON_DIR)
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_icon_full('icon', 'Speechfire — Idle')
        self.indicator.set_menu(self._build_menu())

        # Start global hotkey listener in background thread
        self.listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.listener.daemon = True
        self.listener.start()

        # Check server on startup
        GLib.timeout_add_seconds(2, self._check_server)

        log.info(f'Speechfire Global started. Hotkey: Alt+A. Server: {SERVER_URL}. Language: {LANGUAGE}')

    # --- Menu ---
    def _build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label='Status: Idle')
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        sep = Gtk.SeparatorMenuItem()
        menu.append(sep)

        lang_item = Gtk.MenuItem(label=f'Language: {LANGUAGE}')
        lang_item.set_sensitive(False)
        menu.append(lang_item)

        sep2 = Gtk.SeparatorMenuItem()
        menu.append(sep2)

        quit_item = Gtk.MenuItem(label='Quit Speechfire')
        quit_item.connect('activate', self._quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    # --- Hotkey handling ---
    def _on_key_press(self, key):
        if key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            self.alt_pressed = True
        elif self.alt_pressed and hasattr(key, 'char') and key.char == 'a':
            GLib.idle_add(self._toggle_recording)

    def _on_key_release(self, key):
        if key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            self.alt_pressed = False

    # --- Recording ---
    def _toggle_recording(self):
        if self.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        self.recording = True
        self.audio_frames = []
        self.indicator.set_icon_full('icon-red', 'Speechfire — Recording...')
        self.status_item.set_label('Status: Recording...')
        log.info('Recording started')

        try:
            self.stream = self.pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=self._audio_callback
            )
            self.stream.start_stream()
        except Exception as e:
            log.error(f'Failed to open audio stream: {e}')
            self.recording = False
            GLib.idle_add(self._set_idle)

    def _audio_callback(self, in_data, frame_count, time_info, status):
        if self.recording:
            self.audio_frames.append(in_data)
        return (in_data, pyaudio.paContinue)

    def _stop_recording(self):
        self.recording = False
        log.info('Recording stopped')

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        # Blue = processing/transcribing
        self.indicator.set_icon_full('icon-blue', 'Speechfire — Transcribing...')
        self.status_item.set_label('Status: Transcribing...')

        # Transcribe in background thread
        threading.Thread(target=self._transcribe, daemon=True).start()

    def _transcribe(self):
        if not self.audio_frames:
            log.warning('No audio recorded')
            GLib.idle_add(self._set_idle)
            return

        t0 = time.time()

        # Save to temp WAV file — write raw bytes directly (faster than wave module)
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        try:
            raw_audio = b''.join(self.audio_frames)
            self.audio_frames = []  # Free memory immediately

            with wave.open(tmp.name, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.pa.get_sample_size(FORMAT))
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(raw_audio)

            t1 = time.time()

            # Send to server
            with open(tmp.name, 'rb') as f:
                resp = requests.post(
                    f'{SERVER_URL}/transcribe?lang={LANGUAGE}',
                    files={'audio_data': ('audio.wav', f, 'audio/wav')},
                    timeout=120
                )

            t2 = time.time()

            if resp.status_code == 200:
                text = resp.json().get('transcription', '').strip()
                if text:
                    log.info(f'TIMING: save={t1-t0:.3f}s server={t2-t1:.3f}s total={t2-t0:.3f}s')
                    log.info(f'Transcription: {text}')
                    GLib.idle_add(self._paste_text, text)
                else:
                    log.warning('Empty transcription')
                    GLib.idle_add(self._set_idle)
            else:
                log.error(f'Server error {resp.status_code}: {resp.text}')
                GLib.idle_add(self._set_idle)

        except requests.ConnectionError:
            log.error(f'Cannot connect to server at {SERVER_URL}')
            GLib.idle_add(self._set_error, 'Server offline')
        except Exception as e:
            log.error(f'Transcription failed: {e}')
            GLib.idle_add(self._set_idle)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    # --- Paste into focused app ---
    def _get_focused_wm_class(self):
        """Get WM_CLASS of the focused window via xprop."""
        try:
            win_id = subprocess.run(
                ['xdotool', 'getactivewindow'],
                capture_output=True, text=True, timeout=2
            ).stdout.strip()
            if not win_id:
                return ''
            result = subprocess.run(
                ['xprop', '-id', win_id, 'WM_CLASS'],
                capture_output=True, text=True, timeout=2
            )
            return result.stdout.strip().lower()
        except Exception:
            return ''

    def _is_terminal_focused(self):
        """Detect if the focused window is a terminal emulator."""
        wm_class = self._get_focused_wm_class()
        terminal_keywords = [
            'gnome-terminal', 'terminator', 'xterm', 'konsole',
            'tilix', 'alacritty', 'kitty', 'wezterm',
            'xfce4-terminal', 'mate-terminal', 'lxterminal',
            'guake', 'tilda', 'sakura'
        ]
        is_term = any(t in wm_class for t in terminal_keywords)
        log.info(f'Focused WM_CLASS: {wm_class}, is_terminal: {is_term}')
        return is_term

    def _paste_text(self, text):
        t_paste_start = time.time()
        try:
            # Release any held modifier keys first
            subprocess.run(['xdotool', 'keyup', 'alt', 'Alt_L', 'Alt_R', 'ctrl', 'shift'], check=False)
            time.sleep(0.15)

            # Always use clipboard — faster than xdotool type for any text length
            proc = subprocess.Popen(
                ['xclip', '-selection', 'clipboard'],
                stdin=subprocess.PIPE
            )
            proc.communicate(text.encode('utf-8'))

            is_terminal = self._is_terminal_focused()

            if is_terminal:
                # For terminals: Ctrl+Shift+V via xdotool with explicit key codes
                subprocess.run([
                    'xdotool', 'key', '--clearmodifiers',
                    'ctrl+shift+v'
                ], check=False)
                # Fallback: if ctrl+shift+v didn't work, try xdg approach
                time.sleep(0.1)
                # Verify clipboard still has our text
                verify = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-o'],
                    capture_output=True, text=True, timeout=2
                )
                if verify.stdout.strip() == text.strip():
                    # Clipboard intact but may not have pasted — try xdotool type as fallback
                    # Check if text appeared (we can't easily verify, so trust the first attempt)
                    pass
                log.info(f'Text pasted to TERMINAL via clipboard: "{text[:50]}..."')
            else:
                # For GUI apps: Ctrl+V
                subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+v'], check=True)
                log.info(f'Text pasted to GUI app: "{text[:50]}..."')

            t_paste_end = time.time()
            log.info(f'PASTE TIMING: {t_paste_end - t_paste_start:.3f}s')

        except FileNotFoundError:
            log.error('xclip or xdotool not found. Install: sudo apt install xclip xdotool')
        except Exception as e:
            log.error(f'Paste failed: {e}')

        # Green = success, then back to ready
        self.indicator.set_icon_full('icon-green', 'Speechfire — Ready')
        self.status_item.set_label('Status: Ready (Alt+A to record)')

    # --- UI state ---
    def _set_idle(self):
        self.indicator.set_icon_full('icon-green', 'Speechfire — Ready')
        self.status_item.set_label('Status: Ready (Alt+A to record)')

    def _set_error(self, msg):
        self.indicator.set_icon_full('icon-red', f'Speechfire — Error: {msg}')
        self.status_item.set_label(f'Status: Error — {msg}')

    def _check_server(self):
        try:
            r = requests.get(SERVER_URL, timeout=3)
            if r.status_code == 200:
                log.info('Server is running')
                self.indicator.set_icon_full('icon-green', 'Speechfire — Ready')
                self.status_item.set_label('Status: Ready (Alt+A to record)')
            else:
                self._set_error('Server not responding')
        except Exception:
            self._set_error('Server offline')
        return False  # Don't repeat

    def _quit(self, widget):
        log.info('Shutting down')
        self.listener.stop()
        self.pa.terminate()
        Gtk.main_quit()


def main():
    GLib.set_application_name('Speechfire')
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = SpeechfireGlobal()
    Gtk.main()


if __name__ == '__main__':
    main()
