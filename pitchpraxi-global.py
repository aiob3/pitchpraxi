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
log = logging.getLogger('pitchpraxi')

# --- Paths ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(PROJECT_ROOT, 'extension-firefox', 'icon')
# Support both old (speechfire) and new (pitchpraxi) config dirs
CONFIG_DIR_NEW = Path.home() / '.config' / 'pitchpraxi'
CONFIG_DIR_OLD = Path.home() / '.config' / 'speechfire'
CONFIG_DIR = CONFIG_DIR_NEW if CONFIG_DIR_NEW.exists() else (CONFIG_DIR_OLD if CONFIG_DIR_OLD.exists() else CONFIG_DIR_NEW)
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
    "hotkey_key": "backspace",
    "translate_to_en": False,
}

LANGUAGES = [
    ("pt", "Português"),
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("ja", "日本語"),
    ("zh", "中文"),
    ("ko", "한국어"),
    ("auto", "Auto-detect"),
]

HISTORY_FILE = CONFIG_DIR / 'history.jsonl'
MAX_HISTORY = 50


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


def append_history(entry):
    """Append a transcription to the history file (JSONL)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def load_history(limit=MAX_HISTORY):
    """Load recent history entries."""
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text().strip().split('\n')
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def get_lang_label(code):
    """Get display label for a language code."""
    for c, label in LANGUAGES:
        if c == code:
            return f'{label} ({c})'
    return code


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
        self.indicator.set_icon_full('icon', 'PitchPraxi — Idle')
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

        # ── Status ──
        self.status_item = Gtk.MenuItem(label='Status: Idle')
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        sep0 = Gtk.SeparatorMenuItem()
        menu.append(sep0)

        # ── Language quick-switch (radio group) ──
        lang_header = Gtk.MenuItem(label='── Language ──')
        lang_header.set_sensitive(False)
        menu.append(lang_header)

        self.lang_radios = []
        group = None
        for code, label in LANGUAGES:
            radio = Gtk.RadioMenuItem.new_with_label(
                group.get_group() if group else None,
                f'{label} ({code})'
            )
            if group is None:
                group = radio
            if code == self.config['language']:
                radio.set_active(True)
            radio.connect('toggled', self._on_lang_toggled, code)
            self.lang_radios.append((code, radio))
            menu.append(radio)

        sep1 = Gtk.SeparatorMenuItem()
        menu.append(sep1)

        # ── Translate toggle ──
        self.translate_item = Gtk.CheckMenuItem(label='Translate → English')
        self.translate_item.set_active(self.config.get('translate_to_en', False))
        self.translate_item.connect('toggled', self._toggle_translate)
        menu.append(self.translate_item)

        sep2 = Gtk.SeparatorMenuItem()
        menu.append(sep2)

        # ── Hotkey ──
        self.hotkey_item = Gtk.MenuItem(label=f'Hotkey: {self._hotkey_display()}')
        self.hotkey_item.set_sensitive(False)
        menu.append(self.hotkey_item)

        config_hotkey_item = Gtk.MenuItem(label='Change Hotkey...')
        config_hotkey_item.connect('activate', self._show_hotkey_dialog)
        menu.append(config_hotkey_item)

        sep3 = Gtk.SeparatorMenuItem()
        menu.append(sep3)

        # ── Server ──
        server_header = Gtk.MenuItem(label='── Server ──')
        server_header.set_sensitive(False)
        menu.append(server_header)

        restart_item = Gtk.MenuItem(label='Restart Server')
        restart_item.connect('activate', self._restart_server)
        menu.append(restart_item)

        copy_logs_item = Gtk.MenuItem(label='Copy Recent Logs')
        copy_logs_item.connect('activate', self._copy_logs)
        menu.append(copy_logs_item)

        sep4 = Gtk.SeparatorMenuItem()
        menu.append(sep4)

        # ── History ──
        history_item = Gtk.MenuItem(label='Transcription History...')
        history_item.connect('activate', self._show_history)
        menu.append(history_item)

        sep5 = Gtk.SeparatorMenuItem()
        menu.append(sep5)

        # ── Quit ──
        quit_item = Gtk.MenuItem(label='Quit Speechfire')
        quit_item.connect('activate', self._quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_lang_toggled(self, widget, code):
        if widget.get_active() and code != self.config['language']:
            self.config['language'] = code
            save_config(self.config)
            lang_label = get_lang_label(code)
            log.info(f'Language changed to: {lang_label}')
            # Show notification
            try:
                subprocess.Popen([
                    'notify-send', '-i', 'audio-input-microphone',
                    'PitchPraxi', f'Language: {lang_label}',
                    '-t', '2000'
                ])
            except Exception:
                pass

    def _toggle_translate(self, widget):
        self.config['translate_to_en'] = widget.get_active()
        save_config(self.config)
        mode = 'Translate → EN' if self.config['translate_to_en'] else 'Transcription'
        log.info(f'Mode changed: {mode}')
        try:
            subprocess.Popen([
                'notify-send', '-i', 'audio-input-microphone',
                'PitchPraxi', f'Mode: {mode}',
                '-t', '2000'
            ])
        except Exception:
            pass

    def _restart_server(self, widget):
        """Restart the speechfire.service via systemctl."""
        log.info('Restarting server...')
        self.status_item.set_label('Status: Restarting server...')
        self.indicator.set_icon_full('icon-blue', 'PitchPraxi — Restarting...')

        def do_restart():
            try:
                result = subprocess.run(
                    ['systemctl', '--user', 'restart', 'speechfire.service'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    time.sleep(5)  # Wait for server to load model
                    GLib.idle_add(self._check_server)
                    log.info('Server restarted successfully')
                else:
                    log.error(f'Server restart failed: {result.stderr}')
                    GLib.idle_add(self._set_error, 'Restart failed')
            except Exception as e:
                log.error(f'Server restart error: {e}')
                GLib.idle_add(self._set_error, str(e))

        threading.Thread(target=do_restart, daemon=True).start()

    def _copy_logs(self, widget):
        """Copy last 30 lines of server + global logs to clipboard."""
        try:
            server_logs = subprocess.run(
                ['journalctl', '--user', '-u', 'speechfire.service',
                 '--no-pager', '-n', '15', '--output', 'short-iso'],
                capture_output=True, text=True, timeout=5
            ).stdout

            global_logs = subprocess.run(
                ['journalctl', '--user', '-u', 'speechfire-global.service',
                 '--no-pager', '-n', '15', '--output', 'short-iso'],
                capture_output=True, text=True, timeout=5
            ).stdout

            log_text = (
                '=== Speechfire Server Logs ===\n' + server_logs +
                '\n=== Speechfire Global Logs ===\n' + global_logs
            )

            proc = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
            proc.communicate(log_text.encode('utf-8'))

            log.info('Logs copied to clipboard')
            subprocess.Popen([
                'notify-send', '-i', 'edit-copy',
                'PitchPraxi', 'Logs copied to clipboard (Ctrl+V to paste)',
                '-t', '3000'
            ])
        except Exception as e:
            log.error(f'Failed to copy logs: {e}')

    def _show_history(self, widget):
        """Show transcription history in a dialog."""
        entries = load_history()
        if not entries:
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="No transcription history yet"
            )
            dialog.run()
            dialog.destroy()
            return

        dialog = Gtk.Dialog(
            title='PitchPraxi — Transcription History',
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.set_default_size(700, 500)
        dialog.add_button('Copy All', 1)
        dialog.add_button('Close', Gtk.ResponseType.CLOSE)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        textview.set_left_margin(10)
        textview.set_right_margin(10)
        textview.set_top_margin(10)

        buf = textview.get_buffer()
        lines = []
        for e in reversed(entries):
            ts = e.get('timestamp', '?')
            lang = e.get('language', '?')
            duration = e.get('server_time', '?')
            text = e.get('text', '')
            mode = '→EN' if e.get('translated') else lang
            lines.append(f'[{ts}] ({mode}, {duration}s)\n{text}\n')

        buf.set_text('\n'.join(lines))
        scrolled.add(textview)
        dialog.get_content_area().add(scrolled)
        dialog.show_all()

        response = dialog.run()
        if response == 1:
            # Copy all
            all_text = '\n'.join(lines)
            proc = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
            proc.communicate(all_text.encode('utf-8'))
            log.info('History copied to clipboard')

        dialog.destroy()

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

    # --- Hotkey handling (HOMOLOGADO — nao alterar sem teste) ---
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
        self.indicator.set_icon_full('icon-red', 'PitchPraxi — Recording...')
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

        self.indicator.set_icon_full('icon-blue', 'PitchPraxi — Transcribing...')
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
                    server_time = round(t2 - t1, 2)
                    log.info(f'TIMING: save={t1-t0:.3f}s server={server_time}s total={t2-t0:.3f}s')
                    log.info(f'Transcription: {text}')

                    # Save to history
                    append_history({
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'text': text,
                        'language': self.config['language'],
                        'translated': self.config.get('translate_to_en', False),
                        'server_time': server_time,
                    })

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
        self.indicator.set_icon_full('icon-green', 'PitchPraxi — Ready')
        self.status_item.set_label(f'Status: Ready ({hk} to record)')

    # --- UI state ---
    def _set_idle(self):
        hk = self._hotkey_display()
        self.indicator.set_icon_full('icon-green', 'PitchPraxi — Ready')
        self.status_item.set_label(f'Status: Ready ({hk} to record)')

    def _set_error(self, msg):
        self.indicator.set_icon_full('icon-red', f'PitchPraxi — Error: {msg}')
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
    GLib.set_application_name('PitchPraxi')
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = SpeechfireGlobal()
    Gtk.main()


if __name__ == '__main__':
    main()
