"""PrankBox — moteur d'effets.

Tous les effets (fenêtres, sons, Win32) joués sur ce PC quand quelqu'un du
salon t'envoie une farce. Utilisé par prankbox.py.
Arrêt d'urgence de tous les effets : Ctrl+Alt+Shift+X
"""

import sys

if sys.platform != "win32":
    sys.exit("PrankBox fonctionne uniquement sous Windows.")

import array
import ctypes
import io
import math
import os
import queue
import random
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import wave
import webbrowser
import winsound
from ctypes import wintypes
from dataclasses import dataclass

# Dans le .exe, images et sons sont à côté du programme empaqueté, pas du script.
HERE = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(HERE, "sounds")
IMAGES_DIR = os.path.join(HERE, "images")
GAME_BG = "#0f1923"
GAME_RED = "#ff4655"
GAME_WHITE = "#ece8e1"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32

# ---------------------------------------------------------------- Win32 : écran

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]


def virtual_screen():
    """(x, y, largeur, hauteur) de l'ensemble des écrans."""
    return tuple(user32.GetSystemMetrics(i) for i in
                 (SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN))


def primary_size():
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def screen_scale():
    return primary_size()[1] / 1080


def cursor_pos():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x70, 0x71
VK_CAPITAL = 0x14
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]


def get_mouse_speed():
    speed = ctypes.c_int()
    user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0)
    return speed.value


def set_mouse_speed(speed):
    # Sans SPIF_UPDATEINIFILE : rien n'est enregistré, la vitesse revient à la reconnexion.
    user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(speed), 0)


def caps_lock_on():
    return bool(user32.GetKeyState(VK_CAPITAL) & 1)


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x20, 0x80000
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


def make_click_through(win):
    """Les clics traversent la fenêtre (elle doit déjà être "layered" : -alpha ou -transparentcolor)."""
    win.update_idletasks()
    hwnd = int(win.wm_frame(), 16)
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)


# ------------------------------------------------------- Win32 : rotation écran

ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DM_DISPLAYORIENTATION = 0x00000080
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
user32.ChangeDisplaySettingsW.argtypes = [ctypes.POINTER(DEVMODEW), wintypes.DWORD]


def current_display_mode():
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return None
    return dm


def set_orientation(orientation):
    """0 = normal, 1 = 90°, 2 = 180°, 3 = 270°. Non persistant (annulé au reboot)."""
    dm = current_display_mode()
    if dm is None:
        return False
    if dm.dmDisplayOrientation == orientation:
        return True
    if (dm.dmDisplayOrientation - orientation) % 2:
        dm.dmPelsWidth, dm.dmPelsHeight = dm.dmPelsHeight, dm.dmPelsWidth
    dm.dmDisplayOrientation = orientation
    dm.dmFields = DM_DISPLAYORIENTATION | DM_PELSWIDTH | DM_PELSHEIGHT
    return user32.ChangeDisplaySettingsW(ctypes.byref(dm), 0) == 0


# ------------------------------------------------- Win32 : loupe (zoom, couleurs)

IDENTITY = [1, 0, 0, 0, 0,
            0, 1, 0, 0, 0,
            0, 0, 1, 0, 0,
            0, 0, 0, 1, 0,
            0, 0, 0, 0, 1]
INVERT = [-1, 0, 0, 0, 0,
          0, -1, 0, 0, 0,
          0, 0, -1, 0, 0,
          0, 0, 0, 1, 0,
          1, 1, 1, 0, 1]


def hue_matrix(angle):
    """Matrice de rotation de teinte au format de l'API Magnification (vecteur ligne)."""
    c, s = math.cos(angle), math.sin(angle)
    out_rows = [
        [0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
        [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283],
        [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072],
    ]
    matrix = list(IDENTITY)
    for out in range(3):
        for inp in range(3):
            matrix[inp * 5 + out] = out_rows[out][inp]
    return matrix


class Magnifier:
    """API Magnification de Windows : doit être utilisée depuis le thread Tk."""

    def __init__(self):
        try:
            self.dll = ctypes.WinDLL("Magnification.dll")
            self.dll.MagSetFullscreenTransform.argtypes = [ctypes.c_float, ctypes.c_int, ctypes.c_int]
            self.dll.MagSetFullscreenColorEffect.argtypes = [ctypes.POINTER(ctypes.c_float * 25)]
            self.ok = bool(self.dll.MagInitialize())
        except (OSError, AttributeError):
            self.ok = False

    def transform(self, level, x, y):
        if self.ok:
            self.dll.MagSetFullscreenTransform(level, int(x), int(y))

    def colors(self, matrix):
        if self.ok:
            self.dll.MagSetFullscreenColorEffect(ctypes.byref((ctypes.c_float * 25)(*matrix)))

    def reset_transform(self):
        self.transform(1.0, 0, 0)

    def reset(self):
        self.reset_transform()
        self.colors(IDENTITY)

    def close(self):
        if self.ok:
            self.reset()
            self.dll.MagUninitialize()
            self.ok = False


# ------------------------------------------------------------- Win32 : clavier

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
VK_LWIN, VK_D = 0x5B, 0x44

NAMED_KEYS = {
    "entrée": 0x0D, "entree": 0x0D, "enter": 0x0D, "espace": 0x20, "space": 0x20,
    "échap": 0x1B, "echap": 0x1B, "esc": 0x1B, "tab": 0x09, "retour": 0x08, "backspace": 0x08,
    "maj": 0x10, "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "verr.maj": 0x14, "capslock": 0x14,
    "haut": 0x26, "bas": 0x28, "gauche": 0x25, "droite": 0x27, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "inser": 0x2D, "insert": 0x2D, "suppr": 0x2E, "delete": 0x2E,
    "début": 0x24, "home": 0x24, "fin": 0x23, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
MOUSE_BUTTONS = {  # (appui, relâche)
    "clic": (0x0002, 0x0004), "clic gauche": (0x0002, 0x0004),
    "clic droit": (0x0008, 0x0010), "clic milieu": (0x0020, 0x0040),
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]


def _key(vk=0, scan=0, flags=0):
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)))


def _mouse(flags):
    return INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(dwFlags=flags)))


def send_inputs(*inputs):
    user32.SendInput(len(inputs), (INPUT * len(inputs))(*inputs), ctypes.sizeof(INPUT))


def resolve_key(name):
    """Nom de touche ("G", "espace", "F1", "clic droit"…) -> (type, valeur) ou None."""
    name = name.strip().lower()
    if name in MOUSE_BUTTONS:
        return "mouse", MOUSE_BUTTONS[name]
    if name in NAMED_KEYS:
        return "key", NAMED_KEYS[name]
    if len(name) == 1:
        code = user32.VkKeyScanW(name)  # suit la disposition du clavier (AZERTY, QWERTY…)
        if code != -1:
            return "key", code & 0xFF
    return None


def press(key, hold=0.06):
    kind, value = key
    if kind == "mouse":
        down, up = _mouse(value[0]), _mouse(value[1])
    else:
        # Les scancodes passent mieux que les virtual-keys dans les jeux.
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if value in EXTENDED_VKS else 0)
        scan = user32.MapVirtualKeyW(value, 0)
        down, up = _key(scan=scan, flags=flags), _key(scan=scan, flags=flags | KEYEVENTF_KEYUP)
    send_inputs(down)
    time.sleep(hold)
    send_inputs(up)


def press_combo(*vks):
    downs = [_key(vk=vk) for vk in vks]
    ups = [_key(vk=vk, flags=KEYEVENTF_KEYUP) for vk in reversed(vks)]
    send_inputs(*downs, *ups)


def type_text(text, delay=0.015):
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        unit = int.from_bytes(raw[i:i + 2], "little")
        send_inputs(_key(scan=unit, flags=KEYEVENTF_UNICODE),
                    _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(delay)


# ------------------------------------------------------------------- Raccourci

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
VK_X = 0x58

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]


def listen_panic_hotkey(events):
    """Ctrl+Alt+Shift+X annule tous les effets, même si une fenêtre plein écran bloque tout."""
    if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_X):
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            events.put(("cmd", {"cmd": "stop", "label": "Arrêt d'urgence"}))


# ----------------------------------------------------------------------- Sons

RATE = 22050


def _tone(freq, dur, vol=0.45):
    n = int(dur * RATE)
    fade = max(1, int(0.01 * RATE))
    return [vol * math.sin(2 * math.pi * freq * i / RATE) * min(1.0, i / fade, (n - i) / fade)
            for i in range(n)]


def _silence(dur):
    return [0.0] * int(dur * RATE)


def _melody(notes):
    out = []
    for freq, dur in notes:
        out += _tone(freq, dur) if freq else _silence(dur)
    return out


def _scream(dur=2.2):
    out, phase = [], 0.0
    for i in range(int(dur * RATE)):
        t = i / RATE
        freq = 900 + 400 * math.sin(2 * math.pi * 6 * t) + 300 * t
        phase += 2 * math.pi * freq / RATE
        saw = 2 * ((phase / (2 * math.pi)) % 1) - 1
        envelope = min(1.0, i / (0.01 * RATE), (dur - t) / 0.3)
        out.append((0.6 * saw + 0.4 * random.uniform(-1, 1)) * envelope * 0.95)
    return out


def _glide(f0, f1, dur, vol=0.45):
    n = int(dur * RATE)
    out, phase = [], 0.0
    for i in range(n):
        phase += 2 * math.pi * (f0 + (f1 - f0) * i / n) / RATE
        envelope = min(1.0, i / (0.004 * RATE)) * math.exp(-3 * i / n)
        out.append(vol * envelope * (math.sin(phase) + 0.3 * math.sin(2 * phase)))
    return out


def _bell(freq, dur, vol=0.4):
    n = int(dur * RATE)
    return [vol * min(1.0, i / (0.003 * RATE)) * math.exp(-6 * i / n)
            * (math.sin(2 * math.pi * freq * i / RATE) + 0.4 * math.sin(4 * math.pi * freq * i / RATE))
            for i in range(n)]


def _noise(dur, vol=0.8, lowpass=None, decay=4.0):
    """Bruit blanc (ou grave si lowpass) qui décroît : verre brisé, explosion…"""
    n = int(dur * RATE)
    out, smooth = [], 0.0
    for i in range(n):
        x = random.uniform(-1, 1)
        if lowpass:
            smooth += (x - smooth) * lowpass
            x = smooth * 4
        out.append(vol * x * math.exp(-decay * i / n))
    return out


def _buzz(dur, vol=0.3):
    out, phase = [], 0.0
    for i in range(int(dur * RATE)):
        t = i / RATE
        phase += 2 * math.pi * (620 + 40 * math.sin(2 * math.pi * 5 * t) + 25 * math.sin(2 * math.pi * .7 * t)) / RATE
        saw = 2 * ((phase / (2 * math.pi)) % 1) - 1
        out.append(vol * saw * (0.55 + 0.45 * math.sin(2 * math.pi * 0.5 * t)) * min(1.0, (dur - t) / 0.3))
    return out


def _discord_ring():
    out = []
    for _ in range(3):
        out += _bell(784, .18) + _bell(988, .18) + _bell(1175, .35) + _silence(.6)
    return out


OIIA_SPINS = ((1.47, 3.25), (4.32, 6.36), (7.70, 11.69))  # phases de toupie de la vidéo du chat, en s
OIIA_TURNS_PER_S = 3.7
OIIA_LENGTH = 12.6


def _oiia():
    """Imitation du « oiia oiia » si sounds/oiia.wav manque, sur les mêmes phases que la vidéo."""
    syllable = _glide(330, 700, .1) + _glide(700, 1000, .08) + _glide(1000, 450, .12)
    out = []
    for t0, t1 in OIIA_SPINS:
        out += _silence(t0 - len(out) / RATE)
        while len(out) / RATE + len(syllable) / RATE < t1:
            out += syllable
    return out + _silence(OIIA_LENGTH - len(out) / RATE)


def _to_wav(samples):
    pcm = array.array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


SOUNDS = {
    "boing": lambda: _melody([(180, .08), (240, .08), (320, .12)]),
    "screamer": _scream,
    "notif": lambda: _melody([(1319, .07), (0, .02), (1760, .12)]),
    "erreur": lambda: _melody([(440, .12), (330, .2)]),
    "discord_join": lambda: _glide(520, 780, .09) + _glide(780, 1170, .16),
    "discord_leave": lambda: _glide(1170, 780, .09) + _glide(780, 520, .16),
    "discord_ping": lambda: _bell(1568, .08) + _bell(2093, .25),
    "discord_appel": _discord_ring,
    "moustique": lambda: _buzz(6),
    "verre": lambda: _noise(.9, vol=.7, decay=6) + _bell(3500, .15, .2) + _bell(2800, .2, .15),
    "boom": lambda: _noise(1.6, vol=1.0, lowpass=0.04, decay=3),
    "tic": lambda: _tone(1000, .03, .5),
    "disco": lambda: [s for _ in range(8) for s in _tone(110, .12, .7) + _silence(.13)
                      + _tone(1760, .03, .2) + _silence(.22)],
    "partie_trouvee": lambda: _melody([(880, .12), (0, .03), (1175, .3)]),
    "alerte": lambda: _melody([(988, .15), (0, .1), (988, .15), (0, .1), (988, .15)]),
    "declic": lambda: _melody([(2000, .03), (0, .03), (1500, .05)]),
    "poro": lambda: _glide(900, 1500, .12) + _silence(.04) + _glide(1100, 1800, .18),
    "oiia": _oiia,
}


def play_sound(name):
    """Joue sounds/<name>.wav s'il existe, sinon un son synthétisé."""
    custom = os.path.join(SOUNDS_DIR, name + ".wav")

    def worker():
        if os.path.exists(custom):
            winsound.PlaySound(custom, winsound.SND_FILENAME)
        else:
            winsound.PlaySound(_to_wav(SOUNDS[name]()), winsound.SND_MEMORY)

    threading.Thread(target=worker, daemon=True).start()


winmm = ctypes.windll.winmm
winmm.waveOutSetVolume.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def set_volume(percent):
    """Volume des sons de PrankBox uniquement (session audio de ce programme), 0 à 100."""
    level = int(max(0, min(100, percent)) / 100 * 0xFFFF)
    winmm.waveOutSetVolume(None, (level << 16) | level)


# ------------------------------------------------------------------- Effets

@dataclass
class Overlay:
    win: tk.Toplevel
    canvas: tk.Canvas
    w: int
    h: int
    ox: int  # origine de l'écran principal dans le canvas
    oy: int
    pw: int  # taille de l'écran principal
    ph: int
    scale: float

    @property
    def cx(self):
        return self.ox + self.pw // 2

    @property
    def cy(self):
        return self.oy + self.ph // 2

    @property
    def alive(self):
        return bool(self.win.winfo_exists())


class Effects:
    """Joue les effets sur ce PC.

    on_status(texte) : affiche une info (dernière farce reçue, erreur…).
    """

    def __init__(self, root, on_status):
        self.root = root
        self.on_status = on_status
        self.events = queue.Queue()
        self.mag = Magnifier()
        mode = current_display_mode()
        self.base_orientation = mode.dmDisplayOrientation if mode else 0
        self.rotation_token = 0
        self.gen = 0  # incrémenté par "stop" pour couper les animations en cours
        self.overlays = []
        self.normal_mouse_speed = None  # vitesse d'origine pendant "Souris lente"
        self.tts_proc = None
        self.volume = 80
        threading.Thread(target=listen_panic_hotkey, args=(self.events,), daemon=True).start()
        self._poll()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "cmd":
                    self.run(payload["cmd"], {}, payload.get("label"))
                elif kind == "ui":
                    payload()
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    def run(self, cmd, args, label=None):
        handler = getattr(self, f"fx_{cmd}", None)
        if handler is None:
            return
        try:
            handler(args)
        except Exception as exc:  # un effet raté ne doit pas faire planter l'appli
            self.on_status(f"Erreur sur {label or cmd} : {exc}")

    def shutdown(self):
        self.fx_stop({})
        self.mag.close()

    # ---------- fenêtres d'effet

    def _toplevel(self, x, y, w, h, bg, alpha=None, transparent=False, focus=True, hide_cursor=False,
                  click_through=False):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.configure(bg=bg)
        win.attributes("-topmost", True)
        if click_through and alpha is None and not transparent:
            alpha = 0.99  # rend la fenêtre "layered", nécessaire pour laisser passer les clics
        if alpha is not None:
            win.attributes("-alpha", alpha)
        if transparent:
            win.attributes("-transparentcolor", bg)
        canvas = tk.Canvas(win, bg=bg, highlightthickness=0, width=w, height=h)
        canvas.pack(fill="both", expand=True)
        if hide_cursor:
            try:
                canvas.configure(cursor="none")
            except tk.TclError:
                pass
        win.lift()
        if click_through:
            make_click_through(win)
        elif focus:
            win.focus_force()
        self.overlays.append(win)
        return win, canvas

    def fullscreen(self, bg, **kw):
        vx, vy, vw, vh = virtual_screen()
        pw, ph = primary_size()
        win, canvas = self._toplevel(vx, vy, vw, vh, bg, **kw)
        return Overlay(win, canvas, vw, vh, -vx, -vy, pw, ph, ph / 1080)

    def popup(self, w, h, bg, top=None, **kw):
        pw, ph = primary_size()
        y = top if top is not None else (ph - h) // 2
        win, canvas = self._toplevel((pw - w) // 2, y, w, h, bg, **kw)
        return Overlay(win, canvas, w, h, 0, 0, w, h, ph / 1080)

    def close(self, win):
        if win in self.overlays:
            self.overlays.remove(win)
        try:
            win.destroy()
        except tk.TclError:
            pass

    def rotate(self, orientation, seconds=6):
        self.rotation_token += 1
        token = self.rotation_token
        if not set_orientation(orientation):
            self.on_status("Rotation non supportée par cet écran")
            return

        def restore():
            if token == self.rotation_token:
                self.restore_orientation()

        self.root.after(int(seconds * 1000), restore)

    def restore_orientation(self):
        mode = current_display_mode()
        if mode and mode.dmDisplayOrientation != self.base_orientation:
            set_orientation(self.base_orientation)

    # ---------- effets à l'écran

    def fx_flash(self, a):
        ov = self.fullscreen("white", focus=False)

        def step(i=0):
            if not ov.alive:
                return
            if i >= 12:
                return self.close(ov.win)
            ov.canvas.configure(bg="white" if i % 2 == 0 else "black")
            self.root.after(80, step, i + 1)

        step()

    def fx_retourner(self, a):
        self.rotate((self.base_orientation + 2) % 4)

    def fx_rotation90(self, a):
        self.rotate((self.base_orientation + 1) % 4)

    def fx_inverser(self, a):
        if not self.mag.ok:
            return self.fx_retourner(a)
        gen = self.gen
        self.mag.colors(INVERT)

        def restore():
            if gen == self.gen:
                self.mag.colors(IDENTITY)

        self.root.after(6000, restore)

    def fx_secouer(self, a):
        gen, end = self.gen, time.time() + a.get("duration", 4)
        if self.mag.ok:
            pw, ph = primary_size()
            level = 1.06
            mx, my = int((pw - pw / level) / 2), int((ph - ph / level) / 2)

            def step():
                if gen != self.gen:
                    return
                if time.time() > end:
                    return self.mag.reset_transform()
                self.mag.transform(level, random.randint(0, 2 * mx), random.randint(0, 2 * my))
                self.root.after(25, step)
        else:
            hwnd = user32.GetForegroundWindow()
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))

            def step():
                done = gen != self.gen or time.time() > end
                dx, dy = (0, 0) if done else (random.randint(-25, 25), random.randint(-25, 25))
                user32.SetWindowPos(hwnd, None, rect.left + dx, rect.top + dy, 0, 0,
                                    SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                if not done:
                    self.root.after(25, step)

        step()

    def fx_zoom(self, a):
        if not self.mag.ok:
            self.on_status("Zoom non supporté sur ce PC")
            return
        gen, start = self.gen, time.time()
        pw, ph = primary_size()
        mx, my = cursor_pos()

        def step():
            if gen != self.gen:
                return
            t = time.time() - start
            if t > 5:
                return self.mag.reset_transform()
            level = 1 + 2 * min(1.0, t / 1.5)
            vw, vh = pw / level, ph / level
            x = min(max(mx - vw / 2, 0), pw - vw)
            y = min(max(my - vh / 2, 0), ph - vh)
            self.mag.transform(level, x, y)
            self.root.after(16, step)

        step()

    def fx_ecran_noir(self, a):
        ov = self.fullscreen("black", hide_cursor=True)
        self.root.after(6000, self.close, ov.win)

    def fx_ecran_bleu(self, a):
        ov = self.fullscreen("#0078d7", hide_cursor=True)
        c, s = ov.canvas, ov.scale
        x0, y0 = ov.ox + int(ov.pw * 0.1), ov.oy + int(ov.ph * 0.16)
        wrap = int(ov.pw * 0.7)
        c.create_text(x0, y0, anchor="nw", text=":(", fill="white", font=("Segoe UI Light", -int(150 * s)))
        c.create_text(x0, y0 + int(220 * s), anchor="nw", width=wrap, fill="white",
                      font=("Segoe UI Light", -int(30 * s)),
                      text="Votre PC a rencontré un problème et doit redémarrer. Nous collectons "
                           "simplement des informations relatives aux erreurs, puis nous allons "
                           "redémarrer l'ordinateur.")
        pct = c.create_text(x0, y0 + int(340 * s), anchor="nw", fill="white",
                            font=("Segoe UI Light", -int(30 * s)), text="0% effectué")
        c.create_text(x0, y0 + int(430 * s), anchor="nw", width=wrap, fill="white",
                      font=("Segoe UI", -int(15 * s)),
                      text="Pour en savoir plus sur ce problème et les correctifs possibles, "
                           "visitez https://www.windows.com/stopcode\n\n"
                           "Si vous appelez un support technique, donnez-lui ces informations :\n"
                           "Code d'arrêt : FARCE_ENTRE_POTES")

        def step(p=0):
            if not ov.alive:
                return
            if p >= 100:
                c.itemconfigure(pct, text="100% effectué… je rigole :)")
                return self.root.after(1800, self.close, ov.win)
            c.itemconfigure(pct, text=f"{p}% effectué")
            self.root.after(random.randint(150, 600), step, min(100, p + random.randint(3, 12)))

        step()

    def fx_matrix(self, a):
        ov = self.fullscreen("black", hide_cursor=True)
        c = ov.canvas
        size = max(14, ov.ph // 50)
        font = ("Consolas", -size, "bold")
        chars = "アイウエオカキクケコサシスセソタチツテト0123456789ABCDEF$#@%&"
        cols = ov.w // size
        drops = [random.randint(-ov.h // size, 0) for _ in range(cols)]
        trails = [[] for _ in range(cols)]
        end = time.time() + 8

        def frame():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            for i in range(cols):
                trail = trails[i]
                if trail:
                    c.itemconfigure(trail[-1], fill="#00b33c")
                y = drops[i] * size
                if 0 <= y <= ov.h:
                    trail.append(c.create_text(i * size + size // 2, y, text=random.choice(chars),
                                               fill="#d0ffd0", font=font))
                if len(trail) > 22 or (trail and y > ov.h):
                    c.delete(trail.pop(0))
                drops[i] += 1
                if y > ov.h and random.random() > 0.9:
                    drops[i] = 0
            self.root.after(50, frame)

        frame()

    def fx_confettis(self, a):
        key = "#010203"
        ov = self.fullscreen(key, transparent=True, click_through=True)
        c = ov.canvas
        colors = ["#ff7a1a", "#ffd23f", "#3bceac", "#ee4266", "#540d6e", "#4cc9f0", "#ffffff"]
        pieces = []
        for _ in range(250):
            x, y = random.uniform(0, ov.w), random.uniform(-ov.h, 0)
            size = random.randint(8, 18) * ov.scale
            item = c.create_rectangle(x, y, x + size, y + size * 0.6, fill=random.choice(colors), outline="")
            pieces.append([item, random.uniform(-2, 2), random.uniform(4, 10) * ov.scale])
        end = time.time() + 7

        def frame():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            for piece in pieces:
                piece[1] = max(-4, min(4, piece[1] + random.uniform(-0.6, 0.6)))
                c.move(piece[0], piece[1], piece[2])
            self.root.after(30, frame)

        frame()

    def load_image(self, name, max_h):
        path = os.path.join(IMAGES_DIR, name + ".png")
        if not os.path.exists(path):
            self.on_status(f"Image manquante : images/{name}.png")
            return None
        img = tk.PhotoImage(file=path)
        factor = max(1, math.ceil(img.height() / max_h))
        return img.subsample(factor) if factor > 1 else img

    def fx_pieds1(self, a):
        """Un pied géant tombe au milieu de l'écran et fait trembler l'écran."""
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        img = self.load_image("pieds1", ov.ph * 0.9)
        if img is None:
            return self.close(ov.win)
        ov.canvas.image = img
        foot = ov.canvas.create_image(ov.cx, -img.height(), image=img)
        speed = ov.ph / 12

        def fall():
            if not ov.alive:
                return
            _, y = ov.canvas.coords(foot)
            if y + speed < ov.cy:
                ov.canvas.move(foot, 0, speed)
                return self.root.after(16, fall)
            ov.canvas.coords(foot, ov.cx, ov.cy)
            play_sound("boing")
            self.fx_secouer({"duration": 1})
            self.root.after(3000, self.close, ov.win)

        fall()

    def fx_pieds2(self, a):
        """Des pieds en chaussettes-sandales rebondissent sur l'écran."""
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        img = self.load_image("pieds2", ov.ph * 0.4)
        if img is None:
            return self.close(ov.win)
        ov.canvas.image = img
        half_w, half_h = img.width() / 2, img.height() / 2
        foot = ov.canvas.create_image(ov.cx, ov.cy, image=img)
        vel = [9 * ov.scale, 7 * ov.scale]
        end = time.time() + 8

        def step():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            x, y = ov.canvas.coords(foot)
            if not ov.ox + half_w <= x + vel[0] <= ov.ox + ov.pw - half_w:
                vel[0] = -vel[0]
            if not ov.oy + half_h <= y + vel[1] <= ov.oy + ov.ph - half_h:
                vel[1] = -vel[1]
            ov.canvas.move(foot, *vel)
            self.root.after(16, step)

        step()

    def fx_pieds3(self, a):
        """Pluie de pieds."""
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        img = self.load_image("pieds3", ov.ph * 0.2)
        if img is None:
            return self.close(ov.win)
        ov.canvas.image = img
        feet = [(ov.canvas.create_image(random.uniform(0, ov.w), random.uniform(-2 * ov.h, 0), image=img),
                 random.uniform(6, 14) * ov.scale) for _ in range(30)]
        end = time.time() + 7

        def frame():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            for item, speed in feet:
                ov.canvas.move(item, 0, speed)
            self.root.after(30, frame)

        frame()

    # ---------- spécial jeux

    def fx_faux_lag(self, a):
        gen, end = self.gen, time.time() + 8
        pw, _ = primary_size()
        s = screen_scale()
        w, h = int(360 * s), int(44 * s)
        win, c = self._toplevel(pw - w - int(20 * s), int(20 * s), w, h, GAME_BG, focus=False)
        c.create_text(w // 2, h // 2, text="⚠  PING 999 ms — PERTE DE PAQUETS", fill=GAME_RED,
                      font=("Segoe UI", -int(16 * s), "bold"))
        self.root.after(8000, self.close, win)

        def freeze():
            if gen != self.gen or time.time() > end:
                return
            fx, fy = cursor_pos()
            until = time.time() + random.uniform(0.15, 0.5)

            def hold():
                if gen != self.gen:
                    return
                if time.time() < until:
                    user32.SetCursorPos(fx, fy)
                    self.root.after(5, hold)
                else:
                    self.root.after(random.randint(150, 600), freeze)

            hold()

        freeze()

    def fx_deconnexion(self, a):
        ov = self.fullscreen(GAME_BG, alpha=0.93)
        c, s = ov.canvas, ov.scale
        c.create_text(ov.cx, ov.cy - int(60 * s), text="CONNEXION PERDUE", fill=GAME_RED,
                      font=("Segoe UI Black", -int(64 * s)))
        dots = c.create_text(ov.cx, ov.cy + int(20 * s), text="", fill=GAME_WHITE,
                             font=("Segoe UI", -int(24 * s)))
        c.create_text(ov.cx, ov.cy + int(80 * s), text="Code d'erreur : 0x0000DEAD", fill="#8b978f",
                      font=("Segoe UI", -int(18 * s)))

        def step(i=0):
            if not ov.alive:
                return
            if i >= 28:
                return self.close(ov.win)
            c.itemconfigure(dots, text="Tentative de reconnexion au serveur" + "." * (i % 4))
            self.root.after(250, step, i + 1)

        step()

    def fx_afk(self, a):
        s = screen_scale()
        w, h = int(760 * s), int(140 * s)
        ov = self.popup(w, h, GAME_BG, top=int(60 * s), focus=False)
        c = ov.canvas
        c.create_rectangle(1, 1, w - 2, h - 2, outline=GAME_RED, width=max(2, int(3 * s)))
        c.create_text(w // 2, int(45 * s), text="AVERTISSEMENT INACTIVITÉ", fill=GAME_RED,
                      font=("Segoe UI Black", -int(30 * s)))
        msg = c.create_text(w // 2, int(98 * s), text="", fill=GAME_WHITE,
                            font=("Segoe UI", -int(20 * s), "bold"))
        play_sound("alerte")

        def step(n=10):
            if not ov.alive:
                return
            if n < 0:
                c.itemconfigure(msg, text="Exclu de la partie… ou pas ;)")
                return self.root.after(1800, self.close, ov.win)
            c.itemconfigure(msg, text=f"Tu vas être exclu de la partie dans {n} s")
            self.root.after(1000, step, n - 1)

        step()

    def fx_partie_trouvee(self, a):
        s = screen_scale()
        w, h = int(680 * s), int(250 * s)
        ov = self.popup(w, h, GAME_BG)
        c = ov.canvas
        c.create_text(w // 2, int(80 * s), text="PARTIE TROUVÉE", fill=GAME_WHITE,
                      font=("Segoe UI Black", -int(54 * s)))
        c.create_text(w // 2, int(140 * s), text="PARTIE CLASSÉE  •  10 JOUEURS", fill="#16e5b4",
                      font=("Segoe UI", -int(20 * s), "bold"))
        bar_w, bar_h = int(w * 0.8), max(4, int(8 * s))
        x0, y = (w - bar_w) // 2, int(195 * s)
        c.create_rectangle(x0, y, x0 + bar_w, y + bar_h, fill="#1f2d38", outline="")
        bar = c.create_rectangle(x0, y, x0 + bar_w, y + bar_h, fill="#16e5b4", outline="")
        c.bind("<Button-1>", lambda e: self.close(ov.win))
        play_sound("partie_trouvee")
        start = time.time()

        def step():
            if not ov.alive:
                return
            left = 1 - (time.time() - start) / 6
            if left <= 0:
                return self.close(ov.win)
            c.coords(bar, x0, y, x0 + bar_w * left, y + bar_h)
            self.root.after(30, step)

        step()

    def fx_minimiser(self, a):
        press_combo(VK_LWIN, VK_D)

    def fx_touche(self, a):
        key = resolve_key(str(a.get("key", "")))
        if key is None:
            self.on_status(f"Touche inconnue : {a.get('key')}")
            return
        try:
            hold = min(10.0, max(0.06, float(a.get("hold", 0))))
        except (TypeError, ValueError):
            hold = 0.06
        threading.Thread(target=press, args=(key, hold), daemon=True).start()

    def fx_chat(self, a):
        text = str(a.get("text", ""))[:200]
        if not text:
            return
        open_key = resolve_key(str(a.get("key", "")))
        enter = resolve_key("entrée")

        def worker():
            if open_key:
                press(open_key)
                time.sleep(0.25)
            type_text(text)
            time.sleep(0.1)
            press(enter)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- chaos écran

    def fx_ecran_mort(self, a):
        ov = self.fullscreen("black", hide_cursor=True)
        c, s = ov.canvas, ov.scale
        bw, bh = int(360 * s), int(120 * s)
        x = ov.ox + random.randint(0, ov.pw - bw)
        y = ov.oy + random.randint(0, ov.ph - bh)
        box = c.create_rectangle(x, y, x + bw, y + bh, fill="#1c1c1c", outline="#555555")
        txt = c.create_text(x + bw / 2, y + bh / 2, text="Aucun signal\nVérifiez le câble", fill="#d0d0d0",
                            font=("Segoe UI", -int(26 * s)), justify="center")
        vel = [4 * s, 3 * s]
        end = time.time() + 8

        def step():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            x0, y0, x1, y1 = c.coords(box)
            if x0 + vel[0] < ov.ox or x1 + vel[0] > ov.ox + ov.pw:
                vel[0] = -vel[0]
            if y0 + vel[1] < ov.oy or y1 + vel[1] > ov.oy + ov.ph:
                vel[1] = -vel[1]
            c.move(box, *vel)
            c.move(txt, *vel)
            self.root.after(16, step)

        step()

    def fx_curseur_fou(self, a):
        gen, end = self.gen, time.time() + 5
        vx, vy, vw, vh = virtual_screen()

        def step():
            if gen != self.gen or time.time() > end:
                return
            x, y = cursor_pos()
            user32.SetCursorPos(min(max(x + random.randint(-90, 90), vx), vx + vw - 1),
                                min(max(y + random.randint(-90, 90), vy), vy + vh - 1))
            self.root.after(15, step)

        step()

    def fx_message(self, a):
        text = str(a.get("text", ""))[:300]
        if not text:
            return
        ov = self.fullscreen("black", alpha=0.85)
        ov.canvas.create_text(ov.cx, ov.cy, text=text, fill="white", width=int(ov.pw * 0.8),
                              justify="center", font=("Segoe UI Black", -int(72 * ov.scale)))
        ov.canvas.bind("<Button-1>", lambda e: self.close(ov.win))
        self.root.after(5000, self.close, ov.win)

    # ---------- trucs marrants

    def fx_screamer(self, a):
        faces = [f[:-4] for f in os.listdir(IMAGES_DIR) if f.startswith("screamer") and f.endswith(".png")] \
            if os.path.isdir(IMAGES_DIR) else []
        ov = self.fullscreen("black", hide_cursor=True)
        img = self.load_image(random.choice(faces or ["screamer"]), ov.ph)
        if img is None:
            return self.close(ov.win)
        if img.height() * 2 <= ov.ph:
            img = img.zoom(2)
        ov.canvas.image = img
        ov.canvas.create_image(ov.cx, ov.cy, image=img)
        play_sound("screamer")
        self.fx_secouer({"duration": 2})
        self.root.after(2500, self.close, ov.win)

    def fx_maj_windows(self, a):
        ov = self.fullscreen("black", hide_cursor=True)
        c, s = ov.canvas, ov.scale
        r, dot = 28 * s, max(3, int(4 * s))
        dots = [c.create_oval(0, 0, 0, 0, fill="white", outline="") for _ in range(5)]
        txt = c.create_text(ov.cx, ov.cy + int(90 * s), fill="white", justify="center",
                            font=("Segoe UI", -int(26 * s)))
        start = time.time()

        def step():
            if not ov.alive:
                return
            t = time.time() - start
            pct = min(100, int(t * 8))
            for i, item in enumerate(dots):
                ang = t * 5 - i * 0.35
                x, y = ov.cx + r * math.cos(ang), ov.cy - int(20 * s) + r * math.sin(ang)
                c.coords(item, x - dot, y - dot, x + dot, y + dot)
            if pct >= 100:
                c.itemconfigure(txt, text="Mise à jour terminée… je rigole :)")
                return self.root.after(2000, self.close, ov.win)
            c.itemconfigure(txt, text=f"Mise à jour en cours {pct} %\nNe pas éteindre votre ordinateur. "
                                      "Cela peut prendre un certain temps.")
            self.root.after(30, step)

        step()

    def fx_rickroll(self, a):
        webbrowser.open("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def fx_souris_inversee(self, a):
        gen, end = self.gen, time.time() + 8
        vx, vy, vw, vh = virtual_screen()
        last = list(cursor_pos())

        def step():
            if gen != self.gen or time.time() > end:
                return
            x, y = cursor_pos()
            last[0] = min(max(last[0] - (x - last[0]), vx), vx + vw - 1)
            last[1] = min(max(last[1] - (y - last[1]), vy), vy + vh - 1)
            user32.SetCursorPos(*last)
            self.root.after(5, step)

        step()

    def fx_souris_lente(self, a):
        if self.normal_mouse_speed is None:
            self.normal_mouse_speed = get_mouse_speed()
        set_mouse_speed(1)
        gen = self.gen

        def restore():
            if gen == self.gen:
                self.restore_mouse_speed()

        self.root.after(10000, restore)

    def restore_mouse_speed(self):
        if self.normal_mouse_speed is not None:
            set_mouse_speed(self.normal_mouse_speed)
            self.normal_mouse_speed = None

    def fx_arc_en_ciel(self, a):
        if not self.mag.ok:
            return self.fx_confettis(a)
        gen, start = self.gen, time.time()

        def step():
            if gen != self.gen:
                return
            t = time.time() - start
            if t > 8:
                return self.mag.colors(IDENTITY)
            self.mag.colors(hue_matrix(t * 3))
            self.root.after(30, step)

        step()

    def fx_verr_maj(self, a):
        gen, end = self.gen, time.time() + 12

        def step():
            if gen != self.gen or time.time() > end:
                if caps_lock_on():
                    press_combo(VK_CAPITAL)
                return
            press_combo(VK_CAPITAL)
            self.root.after(random.randint(250, 900), step)

        step()

    NOTIFS = [
        ("Maman", "Descends manger, c'est prêt !!! Ça refroidit !"),
        ("Windows Defender", "Menace détectée : niveau de jeu beaucoup trop faible."),
        ("Discord", "@everyone ton pote vient de te traiter de noob."),
        ("Steam", "Ton jeu a été remboursé… parce que t'es trop nul."),
        ("Batterie", "Batterie faible : il te reste 1 %… de skill."),
        ("Microsoft Teams", "Ton prof a rejoint la réunion et te regarde."),
        ("Météo", "Alerte : risque de rage quit élevé dans ta zone."),
    ]

    def toast(self, app, message, sound="notif", bg="#202020", accent="#cccccc"):
        pw, ph = primary_size()
        s = screen_scale()
        w, h = int(380 * s), int(110 * s)
        x, y = pw - w - int(16 * s), ph - h - int(64 * s)
        win, c = self._toplevel(x, y, w, h, bg, focus=False)
        c.create_rectangle(0, 0, w - 1, h - 1, outline="#3a3a3a")
        c.create_text(int(16 * s), int(22 * s), anchor="w", text=app, fill=accent,
                      font=("Segoe UI Semibold", -int(13 * s)))
        c.create_text(int(16 * s), int(62 * s), anchor="w", width=w - int(32 * s), text=message,
                      fill="white", font=("Segoe UI Semibold", -int(15 * s)))
        c.bind("<Button-1>", lambda e: self.close(win))
        play_sound(sound)
        self.root.after(7000, self.close, win)

    def fx_notif(self, a):
        self.toast(*random.choice(self.NOTIFS))

    def fx_hacker(self, a):
        ov = self.fullscreen("black", hide_cursor=True)
        c, s = ov.canvas, ov.scale
        font = ("Consolas", -int(22 * s))
        lines = [
            "> Connexion à la cible…", "> Contournement du pare-feu… OK",
            "> Accès à la webcam… OK", "> Téléchargement de Photos/Vacances_2019… 100 %",
            "> Lecture de l'historique de navigation… ouh là…",
            "> Envoi de l'historique à Maman… 37 %", "> Envoi de l'historique à Maman… 100 %",
            "> Suppression de toutes les sauvegardes de jeu…", "> Opération terminée.",
        ]
        x0, y0, gap = ov.ox + int(60 * s), ov.oy + int(60 * s), int(36 * s)

        def show(i=0):
            if not ov.alive:
                return
            if i < len(lines):
                c.create_text(x0, y0 + i * gap, anchor="nw", text=lines[i], fill="#00ff66", font=font)
                return self.root.after(random.randint(400, 900), show, i + 1)
            big = c.create_text(ov.cx, ov.cy + int(120 * s), text="PIRATÉ", fill="#ff2a2a",
                                font=("Consolas", -int(140 * s), "bold"))
            blink(big)

        def blink(item, n=0):
            if not ov.alive:
                return
            if n >= 8:
                c.itemconfigure(item, text="je rigole :)", fill="#00ff66")
                return self.root.after(2000, self.close, ov.win)
            c.itemconfigure(item, state="hidden" if n % 2 else "normal")
            self.root.after(300, blink, item, n + 1)

        show()

    ERREURS = [
        "Erreur 404 : skill introuvable.",
        "Le programme « Cerveau.exe » ne répond pas.",
        "Impossible de charger la victoire. Réessayer ?",
        "Vos mains ont rencontré un problème et doivent redémarrer.",
        "Avertissement : trop de défaites détectées.",
        "Fichier « motivation.dll » manquant.",
    ]

    def fx_fausses_erreurs(self, a):
        pw, ph = primary_size()
        s = screen_scale()
        w, h = int(380 * s), int(150 * s)
        gen = self.gen

        def spawn(n=0):
            if gen != self.gen or n >= 12:
                return
            x, y = random.randint(0, pw - w), random.randint(0, ph - h)
            win, c = self._toplevel(x, y, w, h, "#f0f0f0", focus=False)
            c.create_rectangle(0, 0, w, int(30 * s), fill="white", outline="")
            c.create_text(int(10 * s), int(15 * s), anchor="w", text="Erreur", font=("Segoe UI", -int(12 * s)))
            c.create_rectangle(0, 0, w - 1, h - 1, outline="#888888")
            r = int(16 * s)
            c.create_oval(int(20 * s), int(48 * s), int(20 * s) + 2 * r, int(48 * s) + 2 * r,
                          fill="#e81123", outline="")
            c.create_text(int(20 * s) + r, int(48 * s) + r, text="✕", fill="white",
                          font=("Segoe UI", -int(16 * s), "bold"))
            c.create_text(int(68 * s), int(64 * s), anchor="w", width=w - int(84 * s),
                          text=random.choice(self.ERREURS), font=("Segoe UI", -int(13 * s)))
            bx, by = w - int(90 * s), h - int(38 * s)
            ok = c.create_rectangle(bx, by, bx + int(76 * s), by + int(26 * s), fill="#e1e1e1",
                                    outline="#0078d7")
            label = c.create_text(bx + int(38 * s), by + int(13 * s), text="OK", font=("Segoe UI", -int(12 * s)))
            for item in (ok, label):
                c.tag_bind(item, "<Button-1>", lambda e, wn=win: self.close(wn))
            play_sound("erreur")
            self.root.after(15000, self.close, win)
            self.root.after(180, spawn, n + 1)

        spawn()

    def fx_parler(self, a):
        text = str(a.get("text", ""))[:200]
        if not text:
            return
        if self.tts_proc and self.tts_proc.poll() is None:
            self.tts_proc.kill()
        script = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$s.Volume = [int]$env:PRANKBOX_TTS_VOLUME; "
                  "$s.Speak($env:PRANKBOX_TTS)")
        # La voix tourne dans un autre processus : set_volume() ne la concerne pas.
        self.tts_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            env={**os.environ, "PRANKBOX_TTS": text, "PRANKBOX_TTS_VOLUME": str(int(self.volume))},
            creationflags=subprocess.CREATE_NO_WINDOW)

    def set_volume(self, percent):
        self.volume = max(0, min(100, int(percent)))
        set_volume(self.volume)

    # ---------- Discord & sons

    def fx_discord_join(self, a):
        play_sound("discord_join")

    def fx_discord_leave(self, a):
        play_sound("discord_leave")

    def fx_discord_fantome(self, a):
        """Quelqu'un rejoint et quitte le vocal en boucle."""
        gen = self.gen

        def step(n=0):
            if gen != self.gen or n >= 8:
                return
            play_sound("discord_join" if n % 2 == 0 else "discord_leave")
            self.root.after(random.randint(1200, 3500), step, n + 1)

        step()

    DISCORD_MENTIONS = [
        "@toi viens voc vite", "@toi t'es où ???", "@toi pourquoi t'as fait ça", "@everyone URGENT",
        "@toi j'ai vu ce que t'as fait…", "@toi réponds stp",
    ]

    def fx_discord_mention(self, a):
        gen = self.gen

        def step(n=0):
            if gen != self.gen or n >= 4:
                return
            self.toast("Discord", random.choice(self.DISCORD_MENTIONS), sound="discord_ping",
                       bg="#313338", accent="#949cf7")
            self.root.after(random.randint(800, 2500), step, n + 1)

        step()

    def fx_discord_appel(self, a):
        s = screen_scale()
        w, h = int(300 * s), int(330 * s)
        ov = self.popup(w, h, "#232428", top=int(80 * s), focus=False)
        c = ov.canvas
        caller = random.choice(["Maman", "Ton ex", "Le prof", "Numéro inconnu", "Le Père Noël"])
        r = int(48 * s)
        c.create_oval(w // 2 - r, int(40 * s), w // 2 + r, int(40 * s) + 2 * r, fill="#5865f2", outline="")
        c.create_text(w // 2, int(40 * s) + r, text=caller[0], fill="white",
                      font=("Segoe UI Black", -int(40 * s)))
        c.create_text(w // 2, int(175 * s), text=caller, fill="white", font=("Segoe UI Semibold", -int(22 * s)))
        c.create_text(w // 2, int(207 * s), text="Appel entrant…", fill="#b5bac1", font=("Segoe UI", -int(15 * s)))
        br = int(26 * s)
        for cx, color in ((w // 2 - int(60 * s), "#23a55a"), (w // 2 + int(60 * s), "#da373c")):
            button = c.create_oval(cx - br, int(260 * s) - br, cx + br, int(260 * s) + br, fill=color, outline="")
            c.tag_bind(button, "<Button-1>", lambda e: (self.close(ov.win), winsound.PlaySound(None, 0)))
        play_sound("discord_appel")
        self.root.after(8000, self.close, ov.win)

    def fx_moustique(self, a):
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        c, s = ov.canvas, ov.scale
        x, y = ov.cx, ov.cy
        body = c.create_oval(x - 3 * s, y - 2 * s, x + 3 * s, y + 2 * s, fill="#222222", outline="")
        wings = c.create_line(x - 7 * s, y - 5 * s, x, y, x + 7 * s, y - 5 * s, fill="#888888", width=max(1, int(s)))
        vel = [0.0, 0.0]
        end = time.time() + 6
        play_sound("moustique")

        def step():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            vel[0] = max(-14, min(14, vel[0] + random.uniform(-4, 4)))
            vel[1] = max(-14, min(14, vel[1] + random.uniform(-4, 4)))
            bx, by, _, _ = c.coords(body)
            if not ov.ox < bx + vel[0] * s < ov.ox + ov.pw - 10:
                vel[0] = -vel[0]
            if not ov.oy < by + vel[1] * s < ov.oy + ov.ph - 10:
                vel[1] = -vel[1]
            c.move(body, vel[0] * s, vel[1] * s)
            c.move(wings, vel[0] * s, vel[1] * s)
            c.itemconfigure(wings, state="hidden" if random.random() < 0.5 else "normal")
            self.root.after(20, step)

        step()

    # ---------- encore plus

    def poro_frames(self, folder, screen_h):
        """Images du poro pré-calculées par tools/render_poro.py, par nom de fichier (sans .png)."""
        cache = self.__dict__.setdefault("_poro_cache", {})
        if (folder, screen_h) in cache:
            return cache[folder, screen_h]
        path = os.path.join(IMAGES_DIR, folder)
        names = sorted(f for f in os.listdir(path) if f.endswith(".png")) if os.path.isdir(path) else []
        if not names:
            self.on_status(f"Images manquantes : images/{folder}/")
            return {}
        frames = {}
        for name in names:
            img = tk.PhotoImage(file=os.path.join(path, name))
            if screen_h >= 1800:
                img = img.zoom(2)
            elif screen_h < 800:
                img = img.subsample(2)
            frames[name[:-4]] = img
        cache[folder, screen_h] = frames
        return frames

    def fx_poro(self, a):
        """Un poro en 3D traverse l'écran en sautillant, s'arrête pour te regarder et fait une pirouette."""
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        frames = list(self.poro_frames("poro", ov.ph).values())
        if not frames:
            return self.close(ov.win)
        c, s, n = ov.canvas, ov.scale, len(frames)
        w = frames[0].width()
        way = random.choice((-1, 1))
        side = n // 4 if way > 0 else 3 * n // 4  # image où il regarde dans le sens de la marche
        ground = ov.oy + ov.ph * random.uniform(0.65, 0.95)
        middle = ov.ox + ov.pw / 2
        pos = [middle - way * (ov.pw / 2 + w), ground]
        hop_len, hop_h = 170 * s, 110 * s

        def hop(dist, height, ticks, face, spin=0):
            x0 = pos[0]
            for k in range(1, ticks + 1):
                t = k / ticks
                pos[0] = x0 + dist * t
                pos[1] = ground - 4 * height * t * (1 - t)
                yield (face + round(spin * t)) % n
            pos[1] = ground

        def turn(start, end):
            step = 1 if (end - start) % n <= n // 2 else -1
            f = start
            while f != end:
                f = (f + step) % n
                yield f

        def script():
            shown = False
            while -w < (pos[0] - ov.ox) < ov.pw + w or not shown:
                if not shown and (pos[0] - middle) * way > -hop_len:
                    shown = True
                    yield from turn(side, 0)
                    play_sound("poro")
                    yield from [0] * 25
                    yield from hop(0, 330 * s, 42, 0, spin=n)
                    play_sound("poro")
                    yield from [0] * 12
                    yield from turn(0, side)
                yield from hop(way * hop_len, hop_h, 16, side)
                yield from [side] * 4

        frame_ids = script()
        item = c.create_image(*pos, image=frames[side], anchor="s")
        c.image = frames
        end = time.time() + 25

        def tick():
            if not ov.alive:
                return
            f = next(frame_ids, None)
            if f is None or time.time() > end:
                return self.close(ov.win)
            c.coords(item, *pos)
            c.itemconfigure(item, image=frames[f])
            self.root.after(30, tick)

        tick()

    def fx_poro_oiia(self, a):
        """Le chat OIIA, version poro : debout, puis toupie sur la musique, calé sur le son (sounds/oiia.wav)."""
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        frames = self.poro_frames("poro_oiia", ov.ph)
        spin = [img for name, img in frames.items() if name.startswith("toupie")]
        if "debout" not in frames or not spin:
            return self.close(ov.win)
        c = ov.canvas
        item = c.create_image(ov.cx, ov.oy + ov.ph * 0.5 + frames["debout"].height() / 2, image=frames["debout"],
                              anchor="s")
        c.image = frames
        start = time.monotonic()
        play_sound("oiia")

        def tick():
            if not ov.alive:
                return
            t = time.monotonic() - start
            if t > OIIA_LENGTH:
                return self.close(ov.win)
            spinning = [t0 for t0, t1 in OIIA_SPINS if t0 <= t < t1]
            if spinning:
                turns = (t - spinning[0]) * OIIA_TURNS_PER_S
                c.itemconfigure(item, image=spin[int(turns * len(spin)) % len(spin)])
            else:
                c.itemconfigure(item, image=frames["debout"])
            self.root.after(15, tick)

        tick()

    def fx_bouton_fuyant(self, a):
        pw, ph = primary_size()
        s = screen_scale()
        w, h = int(380 * s), int(150 * s)
        win, c = self._toplevel((pw - w) // 2, (ph - h) // 2, w, h, "#f0f0f0", focus=False)
        c.create_rectangle(0, 0, w - 1, h - 1, outline="#0078d7", width=2)
        msg = c.create_text(w // 2, int(45 * s), text="Clique sur le bouton pour arrêter la farce",
                            font=("Segoe UI Semibold", -int(15 * s)))
        button = c.create_rectangle(w // 2 - int(60 * s), int(85 * s), w // 2 + int(60 * s), int(120 * s),
                                    fill="#0078d7", outline="")
        label = c.create_text(w // 2, int(102 * s), text="ARRÊTER", fill="white",
                              font=("Segoe UI", -int(14 * s), "bold"))
        start = time.time()

        def clicked(e):
            c.itemconfigure(msg, text="Bravo, t'as réussi… cette fois.")
            self.root.after(1500, self.close, win)

        for item in (button, label):
            c.tag_bind(item, "<Button-1>", clicked)

        def flee():
            if not win.winfo_exists():
                return
            if time.time() - start > 25:
                return self.close(win)
            if time.time() - start < 12:  # après 12 s il se laisse attraper
                mx, my = cursor_pos()
                x, y = win.winfo_x(), win.winfo_y()
                if x - 40 < mx < x + w + 40 and y - 40 < my < y + h + 40:
                    win.geometry(f"+{random.randint(0, pw - w)}+{random.randint(0, ph - h)}")
            self.root.after(30, flee)

        flee()

    def fx_neige(self, a):
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        c = ov.canvas
        flakes = []
        for _ in range(200):
            r = random.uniform(2, 6) * ov.scale
            x, y = random.uniform(0, ov.w), random.uniform(-ov.h, ov.h)
            flakes.append([c.create_oval(x - r, y - r, x + r, y + r, fill="white", outline=""),
                           random.uniform(1, 4) * ov.scale, random.uniform(0, 6.28)])
        end = time.time() + 10

        def frame():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            for flake in flakes:
                flake[2] += 0.05
                c.move(flake[0], math.sin(flake[2]) * 1.5, flake[1])
                if c.coords(flake[0])[1] > ov.h:
                    c.move(flake[0], 0, -ov.h - 20)
            self.root.after(30, frame)

        frame()

    def fx_disco(self, a):
        ov = self.fullscreen("red", alpha=0.35, click_through=True)
        colors = ["#ff0040", "#00ff80", "#0080ff", "#ffee00", "#ff00ff", "#00ffff"]
        play_sound("disco")
        end = time.time() + 6

        def step():
            if not ov.alive:
                return
            if time.time() > end:
                return self.close(ov.win)
            ov.canvas.configure(bg=random.choice(colors))
            self.root.after(125, step)

        step()

    def fx_ecran_fissure(self, a):
        ov = self.fullscreen("#010203", transparent=True, click_through=True)
        c, s = ov.canvas, ov.scale
        cx = ov.ox + random.uniform(0.25, 0.75) * ov.pw
        cy = ov.oy + random.uniform(0.25, 0.75) * ov.ph
        rings = [[] for _ in range(4)]
        for k in range(16):
            ang = k * 2 * math.pi / 16 + random.uniform(-0.15, 0.15)
            points, x, y = [cx, cy], cx, cy
            for step in range(12):
                ang += random.uniform(-0.25, 0.25)
                dist = random.uniform(30, 90) * s
                x, y = x + math.cos(ang) * dist, y + math.sin(ang) * dist
                points += [x, y]
                if step in (1, 3, 6, 9):
                    rings[(1, 3, 6, 9).index(step)].append((x, y))
            c.create_line(*[p + 1 for p in points], fill="#404040", width=max(1, int(2 * s)))
            c.create_line(*points, fill="#e8f4ff", width=max(1, int(1.5 * s)))
        for ring in rings:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                c.create_line(x1, y1, x2, y2, fill="#d0e4f0", width=max(1, int(s)))
        r = 10 * s
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#e8f4ff", outline="")
        play_sound("verre")
        self.root.after(8000, self.close, ov.win)

    LETTRES = [
        "Je te vois.\n\nNe te retourne pas.\n\n\n\n\n…\n\n\nNon je rigole. Mais joue mieux stp.",
        "Ceci est un message de ton PC.\n\nJ'en ai marre de tes parties.\nJe démissionne.\n\n— Ton PC",
        "ALERTE : ton niveau de jeu a été signalé 47 fois aujourd'hui.\n\nMerci de t'améliorer.",
    ]

    def fx_bloc_notes(self, a):
        path = os.path.join(tempfile.gettempdir(), "LIS_MOI.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(random.choice(self.LETTRES))
        subprocess.Popen(["notepad.exe", path])

    def fx_mode_nuit(self, a):
        if not self.mag.ok:
            return self.fx_ecran_noir(a)
        gen, start = self.gen, time.time()

        def step():
            if gen != self.gen:
                return
            t = time.time() - start
            if t > 10:
                return self.mag.colors(IDENTITY)
            level = max(0.04, 1 - t / 6) if t < 8 else min(1.0, 0.04 + (t - 8) / 2)
            matrix = list(IDENTITY)
            for i in range(3):
                matrix[i * 6] = level
            self.mag.colors(matrix)
            self.root.after(30, step)

        step()

    def fx_pixels_morts(self, a):
        vx, vy, vw, vh = virtual_screen()
        size = max(2, int(3 * screen_scale()))
        for _ in range(6):
            x, y = random.randint(vx, vx + vw - size), random.randint(vy, vy + vh - size)
            color = random.choice(["#ff0000", "#00ff00", "#ffffff", "#000000", "#0000ff"])
            win, _ = self._toplevel(x, y, size, size, color, click_through=True)
            self.root.after(120_000, self.close, win)

    def fx_autodestruction(self, a):
        ov = self.fullscreen("#1a0000", alpha=0.88)
        c, s = ov.canvas, ov.scale
        c.create_text(ov.cx, ov.cy - int(120 * s), text="⚠ AUTODESTRUCTION DU PC ⚠", fill="#ff3030",
                      font=("Segoe UI Black", -int(48 * s)))
        count = c.create_text(ov.cx, ov.cy + int(40 * s), fill="white", font=("Segoe UI Black", -int(200 * s)))

        def step(n=10):
            if not ov.alive:
                return
            if n == 0:
                c.configure(bg="white")
                c.itemconfigure(count, text="BOUM", fill="black")
                play_sound("boom")
                return self.root.after(400, boom_end)
            c.itemconfigure(count, text=str(n))
            c.configure(bg="#1a0000" if n % 2 else "#400000")
            play_sound("tic")
            self.root.after(1000, step, n - 1)

        def boom_end():
            if not ov.alive:
                return
            c.configure(bg="#1a0000")
            c.itemconfigure(count, text="je rigole :)", fill="white", font=("Segoe UI Black", -int(90 * s)))
            self.root.after(2000, self.close, ov.win)

        step()

    # ---------- arrêt

    def fx_stop(self, a):
        self.gen += 1
        self.rotation_token += 1
        for win in list(self.overlays):
            self.close(win)
        self.mag.reset()
        self.restore_orientation()
        self.restore_mouse_speed()
        if self.tts_proc and self.tts_proc.poll() is None:
            self.tts_proc.kill()
        winsound.PlaySound(None, 0)

