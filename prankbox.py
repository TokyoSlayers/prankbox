"""PrankBox — farces en réseau entre potes.

Tout le monde lance ce même programme, choisit un pseudo et rejoint le même
salon avec un code : chacun peut alors troller n'importe qui (ou tout le monde
d'un coup) et se faire troller en retour. Pas besoin d'IP ni d'ouvrir de port,
tout passe par le relais public ntfy.sh.

Usage : python prankbox.py (se met à jour tout seul depuis GitHub au lancement)
Arrêt d'urgence des effets reçus : Ctrl+Alt+Shift+X
"""

import json
import os
import queue
import random
import secrets
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request

import effects
import updater

RELAY = "https://ntfy.sh"
HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURES_DIR = os.path.join(HERE, "captures")
CONFIG_PATH = os.path.join(HERE, "prankbox_config.json")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sans 0/O ni 1/I/L pour éviter les confusions
HEARTBEAT_EVERY = 180  # s : chacun redit "je suis là"
FORGET_AFTER = 420  # s sans nouvelles avant de retirer quelqu'un de la liste

BG = "#160d2e"
CARD = "#21153f"
CARD_BORDER = "#34245e"
ORANGE = "#ff7a1a"
ORANGE_HOVER = "#ff9442"
DARK_BTN = "#3a2a66"
DARK_BTN_HOVER = "#4a3880"
RED = "#ff4655"
RED_HOVER = "#ff6b77"
INPUT_BG = "#2b1d52"
TEXT = "#ffffff"
BTN_TEXT = "#1a0f33"
MUTED = "#a99cc9"

SCREEN_EFFECTS = [
    ("Flash", "flash"), ("Retourner", "retourner"), ("Inverser", "inverser"),
    ("Secouer", "secouer"), ("Écran bleu", "ecran_bleu"), ("Matrix", "matrix"),
    ("Confettis", "confettis"), ("Zoom", "zoom"), ("Écran noir", "ecran_noir"),
    ("Pieds 1", "pieds1"), ("Pieds 2", "pieds2"), ("Pieds 3", "pieds3"),
]
GAME_EFFECTS = [
    ("Faux lag", "faux_lag"), ("Déconnexion", "deconnexion"), ("AFK alerte", "afk"),
    ("Partie trouvée", "partie_trouvee"), ("Minimiser", "minimiser"), ("Capture", "capture"),
]
CHAOS_EFFECTS = [
    ("Écran mort", "ecran_mort"), ("Rotation 90°", "rotation90"), ("Curseur fou", "curseur_fou"),
]
FUN_EFFECTS = [
    ("MAJ Windows", "maj_windows"), ("Rickroll", "rickroll"), ("Faux hacker", "hacker"),
    ("Souris inversée", "souris_inversee"), ("Souris lente", "souris_lente"), ("Arc-en-ciel", "arc_en_ciel"),
    ("Fausse notif", "notif"), ("Fausses erreurs", "fausses_erreurs"), ("Verr. Maj fou", "verr_maj"),
]
DISCORD_EFFECTS = [
    ("Connexion vocal", "discord_join"), ("Déconnexion vocal", "discord_leave"),
    ("Vocal fantôme", "discord_fantome"), ("Mentions", "discord_mention"),
    ("Appel entrant", "discord_appel"), ("Moustique", "moustique"),
]
MORE_EFFECTS = [
    ("Poro", "poro"), ("Poro OIIA", "poro_oiia"), ("Bouton fuyant", "bouton_fuyant"),
    ("Neige", "neige"), ("Disco", "disco"), ("Écran fissuré", "ecran_fissure"),
    ("Bloc-notes", "bloc_notes"), ("Mode nuit", "mode_nuit"), ("Pixels morts", "pixels_morts"),
    ("Autodestruction", "autodestruction"),
]
LABELS = {cmd: label for label, cmd in
          SCREEN_EFFECTS + GAME_EFFECTS + CHAOS_EFFECTS + FUN_EFFECTS + DISCORD_EFFECTS + MORE_EFFECTS}
LABELS.update(touche="Touche", chat="Chat en jeu", message="Message", stop="STOP",
              screamer="SCREAMER", parler="Parler")

RANDOM_POOL = [cmd for _, cmd in
               SCREEN_EFFECTS + CHAOS_EFFECTS + FUN_EFFECTS + DISCORD_EFFECTS + MORE_EFFECTS
               if cmd != "rickroll"] + [
    "faux_lag", "deconnexion", "afk", "partie_trouvee", "screamer"]


# ------------------------------------------------------------------- Réseau

def new_room_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def normalize_room(code):
    """ "abcd-efgh " -> "ABCDEFGH" ; None si le code n'est pas valable."""
    code = "".join(ch for ch in code.upper() if ch.isalnum())
    return code if 4 <= len(code) <= 32 and code.isascii() else None


def format_room(code):
    return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code


def _request(url, data=None, method=None, headers=None, timeout=15):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "PrankBox", **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


class Peer:
    """Un membre du salon. Tout le monde publie et écoute le même sujet ntfy.sh.

    Messages : hello (je suis là), bye (je pars), cmd (une farce, avec la liste
    des cibles ou None = tout le monde sauf l'envoyeur) et capture (pièce jointe
    destinée à celui qui l'a demandée).
    """

    def __init__(self, events):
        self.events = events
        self.id = secrets.token_hex(4)
        self.name = ""
        self.room = None
        self.members = {}  # id -> {"name": str, "seen": float}
        self.lock = threading.Lock()
        self.response = None
        self.outbox = queue.Queue()  # un seul envoyeur : les messages partent dans l'ordre
        for loop in (self._send_loop, self._listen_loop, self._heartbeat_loop):
            threading.Thread(target=loop, daemon=True).start()

    def url(self, room):
        return f"{RELAY}/prankbox-{room.lower()}"

    def join(self, room, name):
        if self.room and self.room != room:
            self._queue({"type": "bye"})
        self.room, self.name = room, name
        with self.lock:
            self.members.clear()
        self.events.put(("members", None))
        self.events.put(("status", "Connexion au salon…"))
        response = self.response
        if response is not None:
            try:
                response.close()  # force la reconnexion sur le nouveau salon
            except OSError:
                pass

    def leave(self):
        if self.room:
            self._post(self.room, self._stamp({"type": "bye"}), timeout=3)

    def snapshot(self):
        with self.lock:
            return sorted(((mid, m["name"]) for mid, m in self.members.items()), key=lambda m: m[1].lower())

    def name_of(self, member_id):
        if member_id == self.id:
            return "toi"
        with self.lock:
            return self.members.get(member_id, {}).get("name", "?")

    def send_cmd(self, cmd, args, label, targets):
        with self.lock:
            count = len(self.members) if targets is None else len(targets & self.members.keys())
        self._queue({"type": "cmd", "cmd": cmd, "args": args or {}, "label": label,
                     "targets": sorted(targets) if targets else None})
        return count

    def send_file(self, data, to):
        meta = json.dumps(self._stamp({"type": "capture", "to": to}))  # ASCII : passe en en-tête HTTP
        _request(self.url(self.room), data=data, method="PUT", timeout=60,
                 headers={"Filename": "capture.jpg", "Message": meta}).close()

    def _stamp(self, payload):
        return {**payload, "id": self.id, "name": self.name}

    def _queue(self, payload):
        if self.room:
            self.outbox.put((self.room, self._stamp(payload)))

    def _send_loop(self):
        while True:
            room, payload = self.outbox.get()
            self._post(room, payload)

    def _post(self, room, payload, timeout=15):
        try:
            _request(self.url(room), data=json.dumps(payload).encode(), timeout=timeout).close()
        except OSError as exc:
            if getattr(exc, "code", None) == 429:
                self.events.put(("log", "Relais : trop d'envois d'un coup, attends quelques secondes"))
            else:
                self.events.put(("log", f"Relais injoignable : {exc}"))

    def _listen_loop(self):
        while True:
            room = self.room
            if room is None:
                time.sleep(0.3)
                continue
            try:
                self.response = _request(self.url(room) + "/json", timeout=90)
                for line in self.response:
                    if room != self.room:
                        break
                    self._handle(json.loads(line))
            except (OSError, ValueError):
                if room == self.room:
                    self.events.put(("status", "Relais injoignable — nouvel essai dans 3 s"))
                    time.sleep(3)

    def _heartbeat_loop(self):
        while True:
            time.sleep(HEARTBEAT_EVERY)
            self._queue({"type": "hello", "new": False})
            limit = time.time() - FORGET_AFTER
            with self.lock:
                gone = [mid for mid, m in self.members.items() if m["seen"] < limit]
                for mid in gone:
                    del self.members[mid]
            if gone:
                self.events.put(("members", None))

    def _handle(self, event):
        if event.get("event") == "open":
            self.events.put(("status", "Connecté au salon"))
            self._queue({"type": "hello", "new": True})
            return
        if event.get("event") != "message":
            return
        try:
            msg = json.loads(event.get("message", ""))
        except ValueError:
            return
        sender = str(msg.get("id", ""))[:16]
        if not sender or sender == self.id:
            return
        name = str(msg.get("name") or "?")[:32]
        kind = msg.get("type")
        with self.lock:
            is_new = sender not in self.members
            if kind == "bye":
                self.members.pop(sender, None)
            else:
                self.members[sender] = {"name": name, "seen": time.time()}
        if kind == "hello":
            if is_new:
                self.events.put(("log", f"{name} a rejoint le salon"))
            if msg.get("new"):
                self._queue({"type": "hello", "new": False})  # le nouveau doit savoir qui est déjà là
        elif kind == "bye" and not is_new:
            self.events.put(("log", f"{name} a quitté le salon"))
        elif kind == "cmd":
            self.events.put(("cmd", msg))
        elif kind == "capture" and msg.get("to") == self.id and event.get("attachment"):
            threading.Thread(target=self._download_capture, args=(name, event["attachment"]["url"]),
                             daemon=True).start()
        self.events.put(("members", None))

    def _download_capture(self, name, url):
        try:
            with _request(url, timeout=60) as resp:
                data = resp.read()
        except OSError as exc:
            self.events.put(("log", f"Capture de {name} perdue : {exc}"))
            return
        save_capture(self.events, name, data)


def save_capture(events, name, data):
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "pc"
    path = os.path.join(CAPTURES_DIR, f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
    with open(path, "wb") as f:
        f.write(data)
    events.put(("capture", (name, path)))


# ------------------------------------------------------------------ Interface

def make_button(parent, text, command, bg=ORANGE, hover=ORANGE_HOVER, fg=BTN_TEXT, size=10):
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=hover,
                    activeforeground=fg, relief="flat", bd=0, cursor="hand2",
                    font=("Segoe UI", size, "bold"), padx=6, pady=8)
    btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
    btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
    return btn


def make_entry(parent, **kw):
    return tk.Entry(parent, bg=INPUT_BG, fg=TEXT, insertbackground=TEXT, relief="flat",
                    highlightthickness=1, highlightbackground=CARD_BORDER, highlightcolor=ORANGE,
                    font=("Segoe UI", 10), **kw)


class PrankBoxApp:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.member_ids = []
        config = self._load_config()
        self.name_var = tk.StringVar(value=config.get("name") or os.environ.get("USERNAME") or socket.gethostname())
        self.room_var = tk.StringVar(value=format_room(config.get("room", "")))
        self.status_var = tk.StringVar(value="Choisis un pseudo, puis rejoins ou crée un salon")
        self.received_var = tk.StringVar(value="Dernière farce reçue : —")
        self.include_me = tk.BooleanVar(value=False)
        self.blocked = tk.BooleanVar(value=False)
        self.volume_var = tk.IntVar(value=config.get("volume", 80))
        self.peer = Peer(self.events)
        self.effects = effects.Effects(root, self.received_var.set, self.send_capture)
        self.effects.set_volume(self.volume_var.get())
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self._poll()
        if normalize_room(self.room_var.get()):
            self.join()

    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def join(self):
        room = normalize_room(self.room_var.get())
        name = self.name_var.get().strip()[:32]
        if not name:
            self.status_var.set("Choisis d'abord un pseudo")
            return
        if room is None:
            self.status_var.set("Code de salon invalide (crée-en un avec « Nouveau salon »)")
            return
        self.room_var.set(format_room(room))
        self.save_config()
        self.peer.join(room, name)
        self.log(f"Tu rejoins le salon en tant que {name}")

    def save_config(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"room": normalize_room(self.room_var.get()) or "",
                       "name": self.name_var.get().strip()[:32], "volume": self.volume_var.get()}, f)

    def on_volume(self, value):
        self.effects.set_volume(int(float(value)))

    def test_volume(self):
        self.save_config()
        effects.play_sound("discord_ping")

    def new_room(self):
        self.room_var.set(format_room(new_room_code()))
        self.join()

    def toggle_room_visibility(self):
        hidden = self.room_entry.cget("show") == ""
        self.room_entry.configure(show="•" if hidden else "")
        self.reveal_button.configure(text="Afficher" if hidden else "Masquer")
        if not hidden:
            self.root.after(10000, lambda: self.room_entry.cget("show") or self.toggle_room_visibility())

    def copy_room(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.room_var.get())
        self.log("Code copié — colle-le à tes potes")

    def shutdown(self):
        self.save_config()
        self.effects.shutdown()
        self.peer.leave()
        self.root.destroy()

    # ---------- construction

    def _build_ui(self):
        root = self.root
        root.title("PrankBox")
        root.configure(bg=BG)
        root.geometry("440x940")
        root.minsize(380, 500)

        canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
        self.body = tk.Frame(canvas, bg=BG)
        body_id = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        header = tk.Frame(self.body, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(header, text="PrankBox", bg=BG, fg=ORANGE, font=("Segoe UI Black", 20)).pack(anchor="w")
        tk.Label(header, text="Trollez-vous les uns les autres", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w")

        self._build_salon_card()
        self._build_screen_card()
        self._build_game_card()
        self._build_chaos_card()
        self._build_fun_card()
        self._button_grid(self._card("Discord & sons"), DISCORD_EFFECTS)
        self._button_grid(self._card("Encore plus de farces"), MORE_EFFECTS)
        self._build_log_card()

    def _card(self, title):
        outer = tk.Frame(self.body, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        outer.pack(fill="x", padx=12, pady=7)
        head = tk.Frame(outer, bg=CARD)
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Frame(head, bg=ORANGE, width=4, height=22).pack(side="left", padx=(0, 10))
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=("Segoe UI", 13, "bold")).pack(side="left")
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="x", padx=10, pady=(0, 12))
        for col in range(3):
            inner.columnconfigure(col, weight=1, uniform="col")
        return inner

    def _button_grid(self, parent, effects, row=0):
        for idx, (label, cmd) in enumerate(effects):
            r, col = divmod(idx, 3)
            make_button(parent, label, lambda c=cmd: self.trigger(c)).grid(
                row=row + r, column=col, sticky="ew", padx=4, pady=4)
        return row + (len(effects) + 2) // 3

    def _hint(self, parent, text, row):
        tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=4, pady=(8, 2))

    def _build_screen_card(self):
        card = self._card("Effets à l'écran")
        row = self._button_grid(card, SCREEN_EFFECTS)
        make_button(card, "Farce aléatoire", self.random_prank, bg=DARK_BTN, hover=DARK_BTN_HOVER,
                    fg=TEXT).grid(row=row, column=0, columnspan=2, sticky="ew", padx=4, pady=(10, 4))
        delay_box = tk.Frame(card, bg=CARD)
        delay_box.grid(row=row, column=2, sticky="ew", padx=4, pady=(4, 4))
        tk.Label(delay_box, text="Différé (min)", bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        self.delay_spin = tk.Spinbox(delay_box, from_=0, to=120, width=5, bg=INPUT_BG, fg=TEXT,
                                     buttonbackground=DARK_BTN, insertbackground=TEXT, relief="flat",
                                     font=("Segoe UI", 11))
        self.delay_spin.pack(fill="x", ipady=3)

    def _build_game_card(self):
        card = self._card("Spécial jeux")
        row = self._button_grid(card, GAME_EFFECTS)

        self._hint(card, "Touche à appuyer (G, espace, F1, clic droit…)", row)
        self.key_entry = make_entry(card, width=8)
        self.key_entry.insert(0, "G")
        self.key_entry.grid(row=row + 1, column=0, sticky="ew", padx=4, pady=4, ipady=6)
        hold_box = tk.Frame(card, bg=CARD)
        hold_box.grid(row=row + 1, column=1, sticky="ew", padx=4)
        tk.Label(hold_box, text="Maintenir (s)", bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        self.hold_spin = tk.Spinbox(hold_box, from_=0, to=10, increment=0.5, width=5, bg=INPUT_BG, fg=TEXT,
                                    buttonbackground=DARK_BTN, insertbackground=TEXT, relief="flat",
                                    font=("Segoe UI", 11))
        self.hold_spin.pack(fill="x", ipady=2)
        make_button(card, "Appuyer", self.send_key).grid(row=row + 1, column=2, sticky="ew", padx=4, pady=4)

        self._hint(card, "Chat en jeu (ouvre le chat, tape, envoie)", row + 2)
        self.chat_entry = make_entry(card)
        self.chat_entry.grid(row=row + 3, column=0, columnspan=2, sticky="ew", padx=4, pady=4, ipady=6)
        self.chat_entry.bind("<Return>", lambda e: self.send_chat())
        make_button(card, "Envoyer", self.send_chat, bg=DARK_BTN, hover=DARK_BTN_HOVER, fg=TEXT).grid(
            row=row + 3, column=2, sticky="ew", padx=4, pady=4)
        tk.Label(card, text="Touche pour ouvrir le chat (vide = aucune) :", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).grid(row=row + 4, column=0, columnspan=2, sticky="w", padx=4)
        self.chat_key_entry = make_entry(card, width=8)
        self.chat_key_entry.insert(0, "entrée")
        self.chat_key_entry.grid(row=row + 4, column=2, sticky="ew", padx=4, pady=4, ipady=3)

    def _build_chaos_card(self):
        card = self._card("Chaos écran")
        row = self._button_grid(card, CHAOS_EFFECTS)
        self._hint(card, "Message géant à l'écran", row)
        self.message_entry = make_entry(card)
        self.message_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=4, pady=4, ipady=6)
        self.message_entry.bind("<Return>", lambda e: self.send_text("message", self.message_entry))
        make_button(card, "Afficher", lambda: self.send_text("message", self.message_entry), bg=DARK_BTN,
                    hover=DARK_BTN_HOVER, fg=TEXT).grid(row=row + 1, column=2, sticky="ew", padx=4, pady=4)

    def _build_fun_card(self):
        card = self._card("Trucs marrants")
        make_button(card, "SCREAMER", lambda: self.trigger("screamer"), bg=RED, hover=RED_HOVER,
                    fg=TEXT, size=12).grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 6))
        row = self._button_grid(card, FUN_EFFECTS, row=1)
        self._hint(card, "Faire parler le PC (voix de Windows)", row)
        self.speak_entry = make_entry(card)
        self.speak_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=4, pady=4, ipady=6)
        self.speak_entry.bind("<Return>", lambda e: self.send_text("parler", self.speak_entry))
        make_button(card, "Parler", lambda: self.send_text("parler", self.speak_entry), bg=DARK_BTN,
                    hover=DARK_BTN_HOVER, fg=TEXT).grid(row=row + 1, column=2, sticky="ew", padx=4, pady=4)

    def _build_log_card(self):
        card = self._card("Journal")
        self.log_box = tk.Text(card, height=7, bg=INPUT_BG, fg=MUTED, relief="flat", wrap="word",
                               font=("Consolas", 9), state="disabled", highlightthickness=0)
        self.log_box.grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=4)

    def _build_salon_card(self):
        card = self._card("Salon")
        for row, (label, var, font) in enumerate((("Ton pseudo", self.name_var, ("Segoe UI", 11)),
                                                  ("Code du salon", self.room_var, ("Consolas", 15, "bold")))):
            tk.Label(card, text=label, bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
                row=row, column=0, sticky="w", padx=4, pady=3)
            entry = make_entry(card, textvariable=var)
            entry.configure(font=font, fg=ORANGE if row else TEXT)
            entry.grid(row=row, column=1, columnspan=1 if row else 2, sticky="ew", padx=4, pady=3, ipady=4)
        # Code masqué par défaut : il ne doit pas apparaître en stream ou en partage d'écran.
        self.room_entry = entry
        entry.configure(show="•")
        self.reveal_button = make_button(card, "Afficher", self.toggle_room_visibility, bg=DARK_BTN,
                                         hover=DARK_BTN_HOVER, fg=TEXT, size=9)
        self.reveal_button.grid(row=1, column=2, sticky="ew", padx=4, pady=3)
        make_button(card, "Rejoindre", self.join).grid(row=2, column=0, sticky="ew", padx=4, pady=(6, 4))
        make_button(card, "Nouveau salon", self.new_room, bg=DARK_BTN, hover=DARK_BTN_HOVER, fg=TEXT,
                    size=9).grid(row=2, column=1, sticky="ew", padx=4, pady=(6, 4))
        make_button(card, "Copier le code", self.copy_room, bg=DARK_BTN, hover=DARK_BTN_HOVER, fg=TEXT,
                    size=9).grid(row=2, column=2, sticky="ew", padx=4, pady=(6, 4))
        tk.Label(card, textvariable=self.status_var, bg=CARD, fg=TEXT, font=("Segoe UI", 9, "bold"),
                 wraplength=360, justify="left").grid(row=3, column=0, columnspan=3, sticky="w", padx=4)

        self.count_var = tk.StringVar(value="Personne d'autre dans le salon")
        tk.Label(card, textvariable=self.count_var, bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).grid(row=4, column=0, columnspan=3, sticky="w", padx=4,
                                                     pady=(8, 0))
        self.members_box = tk.Listbox(card, height=5, selectmode="multiple", exportselection=False,
                                      bg=INPUT_BG, fg=TEXT, selectbackground=ORANGE,
                                      selectforeground=BTN_TEXT, relief="flat", highlightthickness=0,
                                      font=("Segoe UI", 10), activestyle="none")
        self.members_box.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=4)
        tk.Label(card, text="Sélectionne qui troller — aucune sélection = tout le monde sauf toi",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).grid(row=6, column=0, columnspan=3, sticky="w", padx=4)
        for row, (text, var) in enumerate((("M'inclure aussi (pour tester)", self.include_me),
                                           ("Bloquer les farces qu'on m'envoie", self.blocked)), start=7):
            tk.Checkbutton(card, text=text, variable=var, bg=CARD, fg=TEXT, selectcolor=INPUT_BG,
                           activebackground=CARD, activeforeground=TEXT, font=("Segoe UI", 9)).grid(
                row=row, column=0, columnspan=3, sticky="w")
        tk.Label(card, text="Volume des farces reçues (0 = muet)", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=9, column=0, columnspan=3, sticky="w", padx=4, pady=(8, 0))
        tk.Scale(card, from_=0, to=100, orient="horizontal", variable=self.volume_var, command=self.on_volume,
                 bg=CARD, fg=TEXT, troughcolor=INPUT_BG, activebackground=ORANGE, highlightthickness=0,
                 bd=0, sliderrelief="flat", font=("Segoe UI", 8)).grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=4)
        make_button(card, "Tester", self.test_volume, bg=DARK_BTN, hover=DARK_BTN_HOVER, fg=TEXT,
                    size=9).grid(row=10, column=2, sticky="ew", padx=4, pady=(4, 0))
        tk.Label(card, textvariable=self.received_var, bg=CARD, fg=ORANGE, font=("Segoe UI", 9, "bold"),
                 wraplength=360, justify="left").grid(row=11, column=0, columnspan=3, sticky="w", padx=4,
                                                      pady=(4, 0))
        make_button(card, "STOP — annuler les effets chez tout le monde", self.stop_all,
                    bg=RED, hover=RED_HOVER, fg=TEXT).grid(
            row=12, column=0, columnspan=3, sticky="ew", padx=4, pady=(8, 0))

    # ---------- actions

    def selected_targets(self):
        selection = self.members_box.curselection()
        if not selection:
            return None
        return {self.member_ids[i] for i in selection}

    def delay_minutes(self):
        try:
            return max(0, int(self.delay_spin.get()))
        except ValueError:
            return 0

    def trigger(self, cmd, args=None):
        targets = self.selected_targets()
        delay = self.delay_minutes()
        if delay:
            self.root.after(delay * 60_000, self.send, cmd, args, targets)
            self.log(f"{LABELS.get(cmd, cmd)} programmé dans {delay} min")
        else:
            self.send(cmd, args, targets)

    def send(self, cmd, args, targets):
        label = LABELS.get(cmd, cmd)
        if self.peer.room is None:
            self.log("Rejoins d'abord un salon")
            return
        count = self.peer.send_cmd(cmd, args, label, targets)
        who = "tout le monde" if targets is None else ", ".join(self.peer.name_of(t) for t in targets)
        self.log(f"Toi → {who} : {label}" + ("" if count else " (personne d'autre dans le salon)"))
        if self.include_me.get():
            self.receive({"cmd": cmd, "args": args, "label": label, "id": self.peer.id, "name": "toi"})

    def stop_all(self):
        self.effects.run("stop", {})
        self.send("stop", None, None)

    def on_remote_cmd(self, msg):
        label = msg.get("label") or msg.get("cmd")
        targets = msg.get("targets")
        who = "tout le monde" if targets is None else ", ".join(self.peer.name_of(t) for t in targets)
        self.log(f"{msg.get('name', '?')} → {who} : {label}")
        if targets is None or self.peer.id in targets:
            self.receive(msg)

    def receive(self, msg):
        cmd, label, name = msg.get("cmd"), msg.get("label") or msg.get("cmd"), msg.get("name", "?")
        if self.blocked.get() and cmd != "stop":
            self.received_var.set(f"Farce bloquée : {label} (de {name})")
            return
        self.received_var.set(f"Dernière farce reçue : {label} (de {name})")
        self.effects.run(cmd, {**(msg.get("args") or {}), "_from": msg.get("id")}, label)

    def send_capture(self, jpg, requester):
        """Appelé depuis un thread par effects.fx_capture."""
        if requester in (None, self.peer.id):
            save_capture(self.events, "moi", jpg)
        else:
            self.peer.send_file(jpg, requester)

    def send_text(self, cmd, entry):
        text = entry.get().strip()
        if text:
            self.trigger(cmd, {"text": text})
            entry.delete(0, "end")

    def send_key(self):
        key = self.key_entry.get().strip()
        if not key:
            return
        try:
            hold = float(self.hold_spin.get().replace(",", "."))
        except ValueError:
            hold = 0
        self.trigger("touche", {"key": key, "hold": hold})

    def send_chat(self):
        text = self.chat_entry.get().strip()
        if text:
            self.trigger("chat", {"text": text, "key": self.chat_key_entry.get().strip()})
            self.chat_entry.delete(0, "end")

    def random_prank(self):
        self.trigger(random.choice(RANDOM_POOL))

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("1.0", f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log_box.delete("200.0", "end")
        self.log_box.configure(state="disabled")

    def refresh_members(self):
        selected = {self.member_ids[i] for i in self.members_box.curselection()}
        members = self.peer.snapshot()
        self.members_box.delete(0, "end")
        self.member_ids = []
        for mid, name in members:
            self.members_box.insert("end", f"  {name}")
            self.member_ids.append(mid)
            if mid in selected:
                self.members_box.selection_set("end")
        n = len(members)
        self.count_var.set("Personne d'autre dans le salon" if n == 0
                           else f"{n} pote{'s' if n > 1 else ''} dans le salon")

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "members":
                    self.refresh_members()
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "log":
                    self.log(payload)
                elif kind == "cmd":
                    self.on_remote_cmd(payload)
                elif kind == "capture":
                    name, path = payload
                    self.log(f"Capture reçue de {name} : {os.path.basename(path)}")
                    os.startfile(path)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)


def main():
    if os.environ.get("PRANKBOX_NO_UPDATE") != "1" and updater.check():
        # Relance avec les nouveaux fichiers ; la variable évite une boucle si GitHub se contredit.
        env = {**os.environ, "PRANKBOX_NO_UPDATE": "1"}
        subprocess.Popen([sys.executable, os.path.abspath(__file__), *sys.argv[1:]], env=env)
        return
    root = tk.Tk()
    PrankBoxApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
