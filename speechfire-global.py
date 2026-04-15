#!/usr/bin/env python3
"""
Speechfire Global — System-wide voice-to-text for Linux.
Global hotkey captures audio, sends to local whisper.cpp server,
and pastes transcription into the focused application.

Config stored in ~/.config/speechfire/config.json
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

import json
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
from pathlib import Path

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger('speechfire-global')

# --- Paths ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(PROJECT_ROOT, 'extension-firefox', 'icon')
CONFIG_DIR = Path.home() / '.config' / 'speechfire'
CONFIG_FILE = CONFIG_DIR / 'config.json'

# --- Audio constants ---
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 1024
FORMAT = pyaudio.paInt16

# --- Default config ---
DEFAULT_CONFIG = {
    "server_url": "http://127.0.0.1:5000",
    "language": "pt",
    "hotkey_modifier": "alt",
    "hotkey_key": "\\",
    "translate_to_en": False,
}


def load_config():
    """Load config from JSON file, creating defaults if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            # Merge with defaults (add new keys, keep user overrides)
            config = {**DEFAULT_CONFIG, **saved}
            return config
        except Exception as e:
            log.warning(f'Failed to load config: {e}, using defaults')
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save config to JSON file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    log.info(f'Config saved to {CONFIG_FILE}')


class SpeechfireGlobal:
    def __init__(self):
        self.config = load_config()
        self.recording = False
        self.audio_frames = []
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.alt_pressed = False
        self.ctrl_pressed = False
        self.shift_pressed = False

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

        # Start global hotkey listener
        self.listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.listener.daemon = True
        self.listener.start()

        # Check server on startup
        GLib.timeout_add_seconds(2, self._check_server)

        hk = self._hotkey_display()
        log.info(f'Speechfire Global started. Hotkey: {hk}. Server: {self.config["server_url"]}. Language: {self.config["language"]}')

    def _hotkey_display(self):
        mod = self.config["hotkey_modifier"].capitalize()
        key = self.config["hotkey_key"]
        if key == '\\':
            key = '\\'
        return f'{mod}+{key}'

    # --- Menu ---
    def _build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label='Status: Idle')
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        sep0 = Gtk.SeparatorMenuItem()
        menu.append(sep0)

        # Hotkey display + configure
        self.hotkey_item = Gtk.MenuItem(label=f'Hotkey: {self._hotkey_display()}')
        self.hotkey_item.set_sensitive(False)
        menu.append(self.hotkey_item)

        config_hotkey_item = Gtk.MenuItem(label='Change Hotkey...')
        config_hotkey_item.connect('activate', self._show_hotkey_dialog)
        menu.append(config_hotkey_item)

        sep1 = Gtk.SeparatorMenuItem()
        menu.append(sep1)

        # Language
        lang_item = Gtk.MenuItem(label=f'Language: {self.config["language"]}')
        lang_item.set_sensitive(False)
        menu.append(lang_item)

        # Translate toggle
        self.translate_item = Gtk.CheckMenuItem(label='Translate PT → EN')
        self.translate_item.set_active(self.config.get("translate_to_en", False))
        self.translate_item.connect('toggled', self._toggle_translate)
        menu.append(self.translate_item)

        sep2 = Gtk.SeparatorMenuItem()
        menu.append(sep2)

        quit_item = Gtk.MenuItem(label='Quit Speechfire')
        quit_item.connect('activate', self._quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _toggle_translate(self, widget):
        self.config["translate_to_en"] = widget.get_active()
        save_config(self.config)
        mode = "PT → EN" if self.config["translate_to_en"] else "Transcription"
        log.info(f'Mode changed: {mode}')

    def _show_hotkey_dialog(self, widget):
        """Show dialog to capture new hotkey."""
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Press your new hotkey combination..."
        )
        dialog.format_secondary_text(
            f"Current: {self._hotkey_display()}\n\n"
            "Press a modifier (Alt, Ctrl, Shift) + any key.\n"
            "Press Cancel to keep current hotkey."
        )

        self._captured_hotkey = None

        def on_key_press(widget, event):
            from gi.repository import Gdk
            keyval = event.keyval
            state = event.state
            key_name = Gdk.keyval_name(keyval)

            # Ignore bare modifiers
            if key_name in ('Alt_L', 'Alt_R', 'Control_L', 'Control_R', 'Shift_L', 'Shift_R'):
                return False

            modifier = None
            if state & Gdk.ModifierType.MOD1_MASK:
                modifier = 'alt'
            elif state & Gdk.ModifierType.CONTROL_MASK:
                modifier = 'ctrl'
            elif state & Gdk.ModifierType.SHIFT_MASK:
                modifier = 'shift'

            if modifier and key_name:
                self._captured_hotkey = (modifier, key_name.lower())
                dialog.response(Gtk.ResponseType.OK)
            return True

        dialog.connect('key-press-event', on_key_press)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and self._captured_hotkey:
            mod, key = self._captured_hotkey
            self.config["hotkey_modifier"] = mod
            self.config["hotkey_key"] = key
            save_config(self.config)
            self.hotkey_item.set_label(f'Hotkey: {self._hotkey_display()}')
            log.info(f'Hotkey changed to: {self._hotkey_display()}')

    # --- Hotkey handling ---
    def _is_modifier_pressed(self, mod_name):
        if mod_name == 'alt':
            return self.alt_pressed
        elif mod_name == 'ctrl':
            return self.ctrl_pressed
        elif mod_name == 'shift':
            return self.shift_pressed
        return False

    def _on_key_press(self, key):
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_pressed = True
        elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_pressed = True
        elif key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self.shift_pressed = True
        elif self._is_modifier_pressed(self.config["hotkey_modifier"]):
            target_key = self.config["hotkey_key"]
            pressed_key = None
            if hasattr(key, 'char') and key.char:
                pressed_key = key.char
            elif hasattr(key, 'name'):
                pressed_key = key.name

            if pressed_key == target_key:
                GLib.idle_add(self._toggle_recording)

    def _on_key_release(self, key):
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.alt_pressed = False
        elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl_pressed = False
        elif key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self.shift_pressed = False

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

        self.indicator.set_icon_full('icon-blue', 'Speechfire — Transcribing...')
        self.status_item.set_label('Status: Transcribing...')

        threading.Thread(target=self._transcribe, daemon=True).start()

    def _transcribe(self):
        if not self.audio_frames:
            log.warning('No audio recorded')
            GLib.idle_add(self._set_idle)
            return

        t0 = time.time()

        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        try:
            raw_audio = b''.join(self.audio_frames)
            self.audio_frames = []

            with wave.open(tmp.name, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.pa.get_sample_size(FORMAT))
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(raw_audio)

            t1 = time.time()

            # Build request data
            post_data = {
                'response_format': 'json',
                'language': self.config['language'],
            }
            if self.config.get('translate_to_en', False):
                post_data['translate'] = 'true'

            with open(tmp.name, 'rb') as f:
                resp = requests.post(
                    f'{self.config["server_url"]}/inference',
                    files={'file': ('audio.wav', f, 'audio/wav')},
                    data=post_data,
                    timeout=120
                )

            t2 = time.time()

            if resp.status_code == 200:
                data = resp.json()
                text = (data.get('text') or data.get('transcription') or '').strip()
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
            log.error(f'Cannot connect to server at {self.config["server_url"]}')
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
            subprocess.run(['xdotool', 'keyup', 'alt', 'Alt_L', 'Alt_R', 'ctrl', 'shift'], check=False)
            time.sleep(0.15)

            proc = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
            proc.communicate(text.encode('utf-8'))

            is_terminal = self._is_terminal_focused()

            if is_terminal:
                subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+shift+v'], check=False)
                log.info(f'Text pasted to TERMINAL via clipboard: "{text[:50]}..."')
            else:
                subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+v'], check=True)
                log.info(f'Text pasted to GUI app: "{text[:50]}..."')

            log.info(f'PASTE TIMING: {time.time() - t_paste_start:.3f}s')

        except FileNotFoundError:
            log.error('xclip or xdotool not found')
        except Exception as e:
            log.error(f'Paste failed: {e}')

        hk = self._hotkey_display()
        self.indicator.set_icon_full('icon-green', 'Speechfire — Ready')
        self.status_item.set_label(f'Status: Ready ({hk} to record)')

    # --- UI state ---
    def _set_idle(self):
        hk = self._hotkey_display()
        self.indicator.set_icon_full('icon-green', 'Speechfire — Ready')
        self.status_item.set_label(f'Status: Ready ({hk} to record)')

    def _set_error(self, msg):
        self.indicator.set_icon_full('icon-red', f'Speechfire — Error: {msg}')
        self.status_item.set_label(f'Status: Error — {msg}')

    def _check_server(self):
        try:
            r = requests.get(self.config['server_url'], timeout=3)
            if r.status_code == 200:
                log.info('Server is running')
                self._set_idle()
            else:
                self._set_error('Server not responding')
        except Exception:
            self._set_error('Server offline')
        return False

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
