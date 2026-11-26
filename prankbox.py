"""PrankBox — farces en réseau entre potes.

Tout le monde lance ce même programme. Celui qui crée le salon est l'hôte :
les farces passent par son PC, et il peut exclure ou promouvoir des chefs.
On ne partage que le code : l'adresse du PC n'est jamais affichée ni copiée.
Les messages sont chiffrés avec le code du salon.
Deux versions différentes ne peuvent pas rester dans le même salon.

Usage : python prankbox.py
L'ancienne version télécharge toute seule le programme Windows, puis s'efface.
Le .exe se met ensuite à jour tout seul. Arrêt d'urgence : Ctrl+Alt+Shift+X
"""

import hashlib
import hmac
import json
import os
import queue
import random
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import effects
import updater

def _user_dir():
    """Réglages à côté du script, ou dans AppData quand on lance le .exe."""
    if getattr(sys, "frozen", False):
        path = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PrankBox")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


HERE = _user_dir()
DIRECTORY = "https://ntfy.sh"  # annuaire seulement : une adresse chiffrée, jamais les farces
CONFIG_PATH = os.path.join(HERE, "prankbox_config.json")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sans 0/O ni 1/I/L pour éviter les confusions

BG = "#160d2e"
SIDEBAR = "#21153f"
SIDEBAR_HOVER = "#34245e"
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
    ("Partie trouvée", "partie_trouvee"), ("Minimiser", "minimiser"),
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

HOST_PORT = 47777
# À augmenter quand le protocole change. Le salon refuse toute autre valeur.
PROTOCOL = 4
APP_RELEASE = 10  # numéro interne, comparé par les mises à jour déjà installées
APP_VERSION = "9.1"  # numéro affiché. Chaque publication le passe à 9.1, puis 9.2…
MAX_FRAME = 4_000_000
ROLE_LABEL = {"hote": "hôte", "chef": "chef", "membre": "membre"}
PAGES = [
    ("salon", "🏠", "Salon"), ("ecran", "✨", "Écran"), ("jeux", "🎮", "Jeux"),
    ("chaos", "💀", "Chaos"), ("fun", "😄", "Fun"), ("discord", "🎧", "Discord"),
    ("plus", "🎲", "Plus"), ("journal", "📝", "Journal"),
]


def new_room_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def normalize_room(code):
    """ "abcd-efgh " -> "ABCDEFGH" ; None si le code n'est pas valable."""
    code = "".join(ch for ch in code.upper() if ch.isalnum())
    return code if 4 <= len(code) <= 32 and code.isascii() else None


def format_room(code):
    return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code


def code_only(text):
    """Ne garde que le code, même si un ancien texte contenait une adresse après @."""
    code = normalize_room(str(text).partition("@")[0])
    return format_room(code) if code else ""


def _directory_topic(code):
    digest = hashlib.sha256(b"prankbox-annuaire\0" + code.encode()).hexdigest()[:24]
    return "pb-" + digest


def _publish_directory(code, payload):
    data = _seal(room_key(code), payload).hex().encode()
    request = urllib.request.Request(
        f"{DIRECTORY}/{_directory_topic(code)}", data=data, method="POST",
        headers={"User-Agent": "PrankBox", "Cache": "yes"})
    with urllib.request.urlopen(request, timeout=8) as resp:
        resp.read()


def _fetch_directory(code):
    """Dernière annonce chiffrée du salon. [] si l'hôte a fermé, None si rien n'est encore arrivé."""
    url = f"{DIRECTORY}/{_directory_topic(code)}/json?poll=1&since=6h"
    request = urllib.request.Request(url, headers={"User-Agent": "PrankBox"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=8) as resp:
                lines = resp.read().decode().splitlines()
        except OSError:
            lines = []
        messages = []
        key = room_key(code)
        for line in lines:
            try:
                event = json.loads(line)
                msg = _open(key, bytes.fromhex(event.get("message", "")))
            except (ValueError, TypeError):
                continue
            if event.get("event") == "message" and msg:
                messages.append((event.get("time", 0), msg))
        messages.sort()
        if messages:
            closed_at = max((t for t, msg in messages if msg.get("closed")), default=-1)
            hosts = [msg for t, msg in messages if t > closed_at and "join" not in msg and "a" in msg]
            if hosts:
                return _parse_addresses(hosts[-1].get("a"))
            if closed_at >= 0:
                return []
        if attempt < 3:
            time.sleep(0.8)
    return None


def _parse_addresses(items):
    found = []
    for item in items or []:
        host, _, port = str(item).rpartition(":")
        if host and port.isdigit() and 0 < int(port) < 65536 and host != "0.0.0.0":
            found.append((host, int(port)))
    return found


def _udp_send(key, addresses):
    blob = _seal(key, {"a": [f"{host}:{port}" for host, port in addresses]})
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(blob, ("255.255.255.255", HOST_PORT + 1))
    except OSError:
        pass
    finally:
        sock.close()


def _udp_find(code, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.bind(("", HOST_PORT + 1))
    except OSError:
        sock.close()
        return []
    key, limit, found = room_key(code), time.time() + timeout, []
    try:
        while time.time() < limit and not found:
            try:
                data, _ = sock.recvfrom(4096)
            except OSError:
                break
            msg = _open(key, data)
            found = _parse_addresses(msg.get("a")) if isinstance(msg, dict) else []
    finally:
        sock.close()
    return found


def find_host(code):
    """Retrouve le PC de l'hôte à partir du seul code. N'affiche jamais l'adresse."""
    box = queue.Queue()
    threading.Thread(target=lambda: box.put(_udp_find(code, 2.2)), daemon=True).start()
    remote = _fetch_directory(code) or []
    try:
        local = box.get(timeout=2.4)
    except queue.Empty:
        local = []
    seen, ordered = set(), []
    for host, port in local + remote:
        if (host, port) not in seen:
            seen.add((host, port))
            ordered.append((host, port))
    ordered.sort(key=lambda item: not (item[0].startswith(("10.", "192.168.", "127."))))
    return ordered


def room_key(code):
    return hashlib.sha256(b"prankbox-salon-v1\0" + code.encode()).digest()


def _seal(key, payload):
    body = json.dumps(payload, separators=(",", ":")).encode()
    nonce = secrets.token_bytes(16)
    stream = _keystream(key, nonce, len(body))
    cipher = bytes(a ^ b for a, b in zip(body, stream))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    return nonce + mac + cipher


def _open(key, data):
    if len(data) < 32:
        return None
    nonce, mac, cipher = data[:16], data[16:32], data[32:]
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        return None
    stream = _keystream(key, nonce, len(cipher))
    try:
        return json.loads(bytes(a ^ b for a, b in zip(cipher, stream)))
    except ValueError:
        return None


def _keystream(key, nonce, size):
    out, counter = b"", 0
    while len(out) < size:
        out += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:size]


def _lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as resp:
            ip = resp.read().decode().strip()
    except (OSError, ValueError):
        return None
    return ip if ip.count(".") == 3 and ip.replace(".", "").isdigit() else None


def _soap(url, service, action, args):
    fields = "".join(f"<{name}>{value}</{name}>" for name, value in args.items())
    xml = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service}">{fields}</u:{action}></s:Body></s:Envelope>'
    ).encode()
    request = urllib.request.Request(url, data=xml, headers={
        "Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{service}#{action}"',
        "User-Agent": "PrankBox"})
    with urllib.request.urlopen(request, timeout=3) as resp:
        return resp.read()


def _upnp(port, lan):
    """Ouvre le port sur la box (UPnP). Renvoie (ip publique, url, service) ou None."""
    probe = (
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\nMX: 1\r\n'
        "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n"
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.5)
    locations = []
    try:
        sock.sendto(probe, ("239.255.255.250", 1900))
        while True:
            data, _ = sock.recvfrom(65535)
            for line in data.decode("latin1", "replace").split("\r\n"):
                if line.lower().startswith("location:"):
                    locations.append(line.split(":", 1)[1].strip())
    except OSError:
        pass
    finally:
        sock.close()
    for location in locations:
        try:
            return _upnp_open(location, port, lan)
        except (OSError, ValueError, ET.ParseError):
            continue
    return None


def _upnp_open(location, port, lan):
    with urllib.request.urlopen(location, timeout=3) as resp:
        root = ET.fromstring(resp.read())
    base = urllib.parse.urlsplit(location)
    origin = f"{base.scheme}://{base.netloc}"
    for service in root.iter():
        if service.tag.split("}")[-1] != "service":
            continue
        fields = {child.tag.split("}")[-1]: (child.text or "").strip() for child in service}
        kind = fields.get("serviceType", "")
        if not kind.endswith(("WANIPConnection:1", "WANPPPConnection:1")):
            continue
        control = fields.get("controlURL", "")
        if control.startswith("/"):
            control = origin + control
        elif not control.startswith("http"):
            control = origin + "/" + control
        _soap(control, kind, "AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP",
            "NewInternalPort": port, "NewInternalClient": lan, "NewEnabled": 1,
            "NewPortMappingDescription": "PrankBox", "NewLeaseDuration": 0})
        answer = _soap(control, kind, "GetExternalIPAddress", {})
        ip = ET.fromstring(answer).findtext(".//{*}NewExternalIPAddress") or ""
        return ip.strip(), control, kind
    raise ValueError("pas de service WAN")


def _upnp_close(mapping, port):
    if not mapping:
        return
    _, control, kind = mapping
    try:
        _soap(control, kind, "DeletePortMapping", {
            "NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})
    except OSError:
        pass


def _private(ip):
    if ip.startswith(("10.", "192.168.", "127.")):
        return True
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
        except (ValueError, IndexError):
            return False
        return 16 <= second <= 31
    return False


def _stun(sock):
    """Adresse vue depuis l'extérieur, en sortant par un port déjà autorisé (comme le 80)."""
    cookie = 0x2112A442
    for host, port in (("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)):
        tid = secrets.token_bytes(12)
        request = struct.pack("!HHI", 0x0001, 0, cookie) + tid
        try:
            sock.sendto(request, (socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]))
            sock.settimeout(1.5)
            data, _ = sock.recvfrom(2048)
        except OSError:
            continue
        if len(data) < 20 or data[4:8] != cookie.to_bytes(4, "big") or data[8:20] != tid:
            continue
        index = 20
        while index + 4 <= len(data):
            kind, length = struct.unpack_from("!HH", data, index)
            value = data[index + 4:index + 4 + length]
            index += 4 + length + (-length % 4)
            if kind not in (0x0001, 0x0020) or len(value) < 8 or value[1] != 0x01:
                continue
            mapped_port = struct.unpack("!H", value[2:4])[0]
            ip_bytes = value[4:8]
            if kind == 0x0020:
                mapped_port ^= cookie >> 16
                ip_bytes = bytes(b ^ m for b, m in zip(ip_bytes, cookie.to_bytes(4, "big")))
            return ".".join(str(b) for b in ip_bytes), mapped_port
    return None


class _Pipe:
    """Flux fiable par-dessus UDP, pour traverser les box sans port ouvert."""

    CHUNK = 1000

    def __init__(self, sock, peer, conn):
        self.sock = sock
        self.peer = peer
        self.conn = conn
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.closed = False
        self.timeout = None
        self.recv_buf = b""
        self.recv_wait = threading.Condition()
        self.next_send = 0
        self.next_recv = 0
        self.peer_ack = 0
        self.unacked = {}
        self.held = {}
        self._progress = threading.Event()
        threading.Thread(target=self._retransmit, daemon=True).start()

    def settimeout(self, value):
        self.timeout = value
        with self.recv_wait:
            self.recv_wait.notify_all()

    def sendall(self, data):
        with self.lock:
            if self.closed:
                raise OSError("fermé")
            seqs = []
            for offset in range(0, len(data), self.CHUNK):
                self.unacked[self.next_send] = data[offset:offset + self.CHUNK]
                seqs.append(self.next_send)
                self.next_send += 1
        deadline = time.time() + 30
        while time.time() < deadline:
            with self.lock:
                if self.closed:
                    raise OSError("fermé")
                left = [seq for seq in seqs if seq in self.unacked]
                if not left:
                    return
                window = [seq for seq in left if seq < self.peer_ack + 16]
            for seq in window:
                self._send(4, seq, self.unacked.get(seq, b""))
            self._progress.wait(0.15)
            self._progress.clear()
        raise OSError("envoi perdu")

    def recv(self, size):
        deadline = None if self.timeout is None else time.time() + self.timeout
        with self.recv_wait:
            while not self.recv_buf:
                if self.closed:
                    return b""
                remaining = None if deadline is None else deadline - time.time()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("délai")
                self.recv_wait.wait(0.5 if remaining is None else remaining)
            take = min(size, len(self.recv_buf))
            out, self.recv_buf = self.recv_buf[:take], self.recv_buf[take:]
            return out

    def close(self):
        with self.lock:
            already = self.closed
            self.closed = True
        if not already:
            try:
                self._send(8, 0, b"")
            except OSError:
                pass
        with self.recv_wait:
            self.recv_wait.notify_all()

    def feed(self, packet):
        if len(packet) < 19 or packet[:2] != b"PB":
            return False
        flags, conn, seq, ack = struct.unpack_from("!B8sII", packet, 2)
        if conn != self.conn:
            return False
        payload = packet[19:]
        fresh = []
        with self.lock:
            self.ready.set()
            if ack > self.peer_ack:
                self.peer_ack = ack
                for old in [item for item in self.unacked if item < ack]:
                    del self.unacked[old]
                self._progress.set()
            if flags & 8:
                self.closed = True
            if flags & 4 and self.next_recv <= seq < self.next_recv + 64:
                self.held[seq] = payload
                while self.next_recv in self.held:
                    fresh.append(self.held.pop(self.next_recv))
                    self.next_recv += 1
        if fresh or flags & 8:
            with self.recv_wait:
                self.recv_buf += b"".join(fresh)
                self.recv_wait.notify_all()
        if flags & 4:
            self._send(2, 0, b"")
        return True

    def _send(self, flags, seq, payload):
        with self.lock:
            ack = self.next_recv
            peer = self.peer
        packet = b"PB" + struct.pack("!B8sII", flags, self.conn, seq, ack) + payload
        self.sock.sendto(packet, peer)

    def _retransmit(self):
        while not self.closed:
            time.sleep(0.15)
            if not self.ready.is_set():
                self._send(1, 0, b"")
                continue
            with self.lock:
                window = [seq for seq in self.unacked if seq < self.peer_ack + 16]
            for seq in window:
                self._send(4, seq, self.unacked.get(seq, b""))


class _Link:
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.out_n = 0
        self.in_n = 0


def _read_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise OSError("connexion fermée")
        buf += chunk
    return buf


def _read_msg(link, key):
    size = struct.unpack(">I", _read_exact(link.sock, 4))[0]
    if size > MAX_FRAME:
        raise OSError("message trop gros")
    msg = _open(key, _read_exact(link.sock, size))
    number = msg.get("n") if isinstance(msg, dict) else None
    if not isinstance(number, int) or number <= link.in_n:
        raise OSError("message refusé")
    link.in_n = number
    return msg


def _write_msg(link, key, payload):
    with link.lock:
        link.out_n += 1
        payload = {**payload, "n": link.out_n}
        data = _seal(key, payload)
        link.sock.sendall(struct.pack(">I", len(data)) + data)


class Peer:
    """Salon hébergé par le PC qui l'a créé. Les autres s'y connectent directement.

    L'hôte relaie les farces. Les chefs (l'hôte, et ceux qu'il promeut) peuvent
    exclure un membre, en promouvoir un autre et régler le délai entre deux farces.
    Seul l'hôte retire le rôle de chef. Le trafic s'arrête si l'hôte ferme.
    """

    def __init__(self, events):
        self.events = events
        self.id = secrets.token_hex(4)
        self.name = ""
        self.room = None
        self.role = None
        self.members = {}  # id -> {"name", "role"} (les autres, pas soi)
        self.lock = threading.Lock()
        self.server = None
        self.upnp = None
        self.port = None
        self.link = None
        self.clients = {}  # id -> _Link, côté hôte
        self.banned = set()
        self.cooldown = 0  # secondes entre deux farces, 0 = aucun délai
        self.last_cmd = {}  # id -> instant du dernier envoi
        self.blocked_until = {}  # id -> instant jusqu'auquel l'hôte a refusé
        self._joined = threading.Event()
        self._refused = None
        self.addresses = []
        self.udp = None
        self._announcing = False
        self._announce_stop = None

    def host(self, room, name, discover=True):
        """Ouvre le salon sur ce PC. L'adresse est annoncée chiffrée, jamais affichée."""
        self.leave()
        self.room, self.name, self.role = room, name, "hote"
        self.key = room_key(room)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", HOST_PORT))
        except OSError:
            server.bind(("0.0.0.0", 0))
        self.port = server.getsockname()[1]
        server.listen(8)
        self.server = server
        threading.Thread(target=self._accept, args=(server,), daemon=True).start()
        lan = "127.0.0.1"
        addresses = [(lan, self.port)]
        if discover:
            lan = _lan_ip()
            addresses = [(lan, self.port)]
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.bind(("0.0.0.0", self.port))
            self.udp = udp
            self.upnp = _upnp(self.port, lan)
            reflex = _stun(udp)
            if reflex and reflex != (lan, self.port):
                addresses.append(reflex)
            elif not reflex:
                public = (self.upnp[0] if self.upnp else None) or _public_ip()
                if public and public != lan:
                    addresses.append((public, self.port))
            threading.Thread(target=self._udp_demux, args=(udp,), daemon=True).start()
        self.addresses = addresses
        if discover:
            self._announcing = True
            self._announce_stop = threading.Event()
            threading.Thread(target=self._announce_loop, args=(self._announce_stop, server), daemon=True).start()
            threading.Thread(target=self._watch_joins, args=(self._announce_stop,), daemon=True).start()
        self.events.put(("members", None))
        self.events.put(("status", "Tu es l'hôte. Tes potes traversent leur box tout seuls, sans port à ouvrir."))
        self.events.put(("log", "Laisse PrankBox ouvert. Tes potes n'ont besoin que du code."))
        return addresses

    def _announce_loop(self, stop, server):
        next_http, warned = 0, False
        while not stop.is_set() and self.server is server and self._announcing:
            _udp_send(self.key, self.addresses)
            if time.time() >= next_http:
                try:
                    _publish_directory(self.room, {"a": [f"{host}:{port}" for host, port in self.addresses]})
                    next_http, warned = time.time() + 480, False
                except OSError:
                    next_http = time.time() + 20
                    if not warned:
                        self.events.put(("log", "Tes potes éloignés ne peuvent pas encore te trouver, nouvel essai"))
                        warned = True
            stop.wait(2)

    def _watch_joins(self, stop):
        seen = set()
        topic = _directory_topic(self.room)
        while not stop.is_set() and self._announcing:
            request = urllib.request.Request(
                f"{DIRECTORY}/{topic}/json?since=30s", headers={"User-Agent": "PrankBox"})
            try:
                with urllib.request.urlopen(request, timeout=70) as resp:
                    for raw in resp:
                        if stop.is_set():
                            return
                        try:
                            event = json.loads(raw.decode())
                            msg = _open(self.key, bytes.fromhex(event.get("message", "")))
                        except (ValueError, TypeError):
                            continue
                        who = msg.get("join") if isinstance(msg, dict) else None
                        if who and who not in seen:
                            seen.add(who)
                            for addr in _parse_addresses(msg.get("a")):
                                self._knock(addr)
            except OSError:
                stop.wait(2)

    def _knock(self, addr):
        """Ouvre le chemin dans la box vers ce pote, sans connaître encore sa connexion."""
        packet = b"PB" + struct.pack("!B8sII", 1, b"\0" * 8, 0, 0)

        def go():
            end = time.time() + 6
            while time.time() < end and self._announcing and self.udp is not None:
                try:
                    self.udp.sendto(packet, addr)
                except OSError:
                    return
                time.sleep(0.2)

        threading.Thread(target=go, daemon=True).start()

    def _udp_demux(self, sock):
        pipes = {}
        sock.settimeout(0.5)
        while self.udp is sock:
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            if len(data) < 19 or data[:2] != b"PB":
                continue
            _flags, conn, _seq, _ack = struct.unpack_from("!B8sII", data, 2)
            if conn == b"\0" * 8:
                continue
            pipe = pipes.get(conn)
            if pipe is None:
                pipe = _Pipe(sock, addr, conn)
                pipe.ready.set()
                pipes[conn] = pipe
                try:
                    pipe._send(1, 0, b"")
                except OSError:
                    pass
                threading.Thread(target=self._session, args=(pipe,), daemon=True).start()
            pipe.peer = addr
            pipe.feed(data)

    def connect(self, room, name, addresses):
        self.leave()
        self.room, self.name, self.role = room, name, "membre"
        self.key = room_key(room)
        private = [item for item in addresses if _private(item[0])]
        public = [item for item in addresses if item not in private]
        for group, delay in ((private, 2), (public, 2)):
            for host, port in group:
                try:
                    sock = socket.create_connection((host, port), timeout=delay)
                except OSError:
                    continue
                if self._attach(sock):
                    return True
                if self._refused == "version":
                    self.room = self.role = None
                    return False
        pipe = self._punch(public)
        if pipe is not None and self._attach(pipe):
            return True
        self.room = self.role = None
        if self._refused != "version":
            self.events.put(("status", "Hôte injoignable. Il doit laisser PrankBox ouvert."))
        return False

    def _attach(self, sock):
        sock.settimeout(None)
        link = _Link(sock)
        self.link = link
        self._joined.clear()
        self._refused = None
        try:
            _write_msg(link, self.key, {"type": "hello", "id": self.id, "name": self.name, "v": PROTOCOL})
        except OSError:
            self.link = None
            sock.close()
            return False
        threading.Thread(target=self._listen, args=(link,), daemon=True).start()
        if not self._joined.wait(8) or self.link is not link or self._refused:
            if self.link is link:
                self.link = None
                sock.close()
            return False
        self.events.put(("status", "Connecté au salon"))
        return True

    def _punch(self, addresses):
        if not addresses:
            return None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        reflex = _stun(sock)
        if reflex:
            try:
                _publish_directory(self.room, {"join": self.id, "a": [f"{reflex[0]}:{reflex[1]}"]})
            except OSError:
                pass
        conn = secrets.token_bytes(8)
        pipe = _Pipe(sock, addresses[0], conn)

        def reader():
            sock.settimeout(0.5)
            while not pipe.closed:
                try:
                    data, addr = sock.recvfrom(4096)
                except TimeoutError:
                    continue
                except OSError:
                    break
                pipe.peer = addr
                pipe.feed(data)

        threading.Thread(target=reader, daemon=True).start()
        if pipe.ready.wait(6):
            return pipe
        pipe.close()
        sock.close()
        return None

    def leave(self):
        server, link, port, mapping = self.server, self.link, self.port, self.upnp
        udp = self.udp
        self.udp = None
        room, key, announcing = self.room, getattr(self, "key", None), self._announcing
        stop = self._announce_stop
        self._announcing = False
        if stop is not None:
            stop.set()
        self.server = self.link = self.upnp = None
        self.room = self.role = None
        if announcing and room and key:
            try:
                _publish_directory(room, {"closed": True})
            except OSError:
                pass
        with self.lock:
            clients = list(self.clients.values())
            self.clients.clear()
            self.members.clear()
            self.banned.clear()
            self.last_cmd.clear()
            self.blocked_until.clear()
            self.cooldown = 0
            self._refused = None
        self._joined.set()
        for client in clients:
            try:
                _write_msg(client, self.key, {"type": "closed", "id": self.id, "name": self.name})
            except OSError:
                pass
            client.sock.close()
        if link is not None:
            try:
                _write_msg(link, self.key, {"type": "bye", "id": self.id, "name": self.name})
            except OSError:
                pass
            link.sock.close()
        if server is not None:
            server.close()
        if udp is not None:
            udp.close()
        if mapping:
            closer = threading.Thread(target=_upnp_close, args=(mapping, port))
            closer.start()
            closer.join(3)
        self.events.put(("members", None))

    def snapshot(self):
        with self.lock:
            rows = [(mid, m["name"], m["role"]) for mid, m in self.members.items()]
        return sorted(rows, key=lambda row: (row[2] != "hote", row[1].lower()))

    def name_of(self, member_id):
        if member_id == self.id:
            return "toi"
        with self.lock:
            return self.members.get(member_id, {}).get("name", "?")

    def send_cmd(self, cmd, args, label, targets):
        if cmd != "stop":
            left = self._mark_send(self.id)
            if left > 0:
                self.events.put(("status", f"Attends {max(1, round(left))} s avant la prochaine farce"))
                return None
        with self.lock:
            known = set(self.members)
            count = len(known) if targets is None else len(targets & known)
        self._relay({"type": "cmd", "cmd": cmd, "args": args or {}, "label": label,
                     "targets": sorted(targets) if targets else None, "id": self.id, "name": self.name},
                    skip=self.id)
        return count

    def set_cooldown(self, seconds):
        if self.role == "hote":
            self._set_cooldown(self.id, seconds)
        elif self.role == "chef" and self.link is not None:
            self._send(self.link, {"type": "cooldown", "seconds": int(seconds),
                                   "id": self.id, "name": self.name})

    def admin(self, action, target):
        if self.role == "hote":
            self._admin(self.id, action, target)
        elif self.link is not None:
            self._send(self.link, {"type": action, "target": target, "id": self.id, "name": self.name})

    def _relay(self, payload, skip, only=None):
        """Envoie aux clients (côté hôte) ou à l'hôte (côté invité)."""
        if self.server is not None:
            with self.lock:
                links = [link for mid, link in self.clients.items() if mid != skip and (only in (None, mid))]
            for link in links:
                self._send(link, payload)
        elif self.link is not None:
            self._send(self.link, payload)

    def _send(self, link, payload):
        try:
            _write_msg(link, self.key, payload)
        except OSError:
            pass

    def _accept(self, server):
        while self.server is server:
            try:
                sock, _ = server.accept()
            except OSError:
                break
            threading.Thread(target=self._session, args=(sock,), daemon=True).start()

    def _session(self, sock):
        sock.settimeout(8)
        link = _Link(sock)
        try:
            hello = _read_msg(link, self.key)
        except OSError:
            sock.close()
            return
        sock.settimeout(None)
        member = str(hello.get("id", ""))[:16]
        name = str(hello.get("name") or "?")[:32]
        if hello.get("type") != "hello" or not member or member == self.id or name.casefold() in self.banned:
            try:
                _write_msg(link, self.key, {"type": "kicked", "id": self.id, "name": self.name,
                                            "reason": "exclu de ce salon" if name.casefold() in self.banned
                                            else "invitation refusée"})
            except OSError:
                pass
            sock.close()
            return
        if hello.get("v") != PROTOCOL:
            try:
                _write_msg(link, self.key, {"type": "version", "v": PROTOCOL,
                                            "id": self.id, "name": self.name})
            except OSError:
                pass
            sock.close()
            self.events.put(("log", f"{name} n'a pas la même version : connexion refusée"))
            return
        with self.lock:
            self.clients[member] = link
            self.members[member] = {"name": name, "role": "membre"}
        self.events.put(("log", f"{name} a rejoint le salon"))
        self._broadcast_members()
        try:
            while self.server is not None:
                msg = _read_msg(link, self.key)
                if str(msg.get("id", ""))[:16] != member:
                    break
                self._from_client(member, msg)
        except OSError:
            pass
        finally:
            sock.close()
            with self.lock:
                gone = self.clients.pop(member, None) is not None
                info = self.members.pop(member, None)
            if gone and info:
                if info["name"].casefold() not in self.banned:
                    self.events.put(("log", f"{info['name']} a quitté le salon"))
                self._broadcast_members()

    def _from_client(self, member, msg):
        kind = msg.get("type")
        if kind == "bye":
            raise OSError("bye")
        if kind in ("kick", "promote", "demote"):
            self._admin(member, kind, str(msg.get("target", ""))[:16])
        elif kind == "cooldown":
            self._set_cooldown(member, msg.get("seconds", 0))
        elif kind == "cmd":
            clean = self._clean_cmd(msg)
            if not clean:
                return
            if clean["cmd"] != "stop":
                left = self._mark_send(member)
                if left > 0:
                    with self.lock:
                        link = self.clients.get(member)
                    if link:
                        self._send(link, {"type": "wait", "left": max(1, round(left)),
                                          "id": self.id, "name": self.name})
                    return
            self._relay(clean, skip=member)
            self.events.put(("cmd", clean))

    def _listen(self, link):
        try:
            while self.link is link:
                msg = _read_msg(link, self.key)
                kind = msg.get("type")
                if kind == "version" or (kind == "members" and msg.get("v") != PROTOCOL):
                    self._refused = "version"
                    self._joined.set()
                    self.events.put(("status", "Versions différentes. Tout le salon doit avoir la même mise à jour : relance PrankBox."))
                    self.events.put(("log", "Connexion refusée : pas la même version que l'hôte"))
                    break
                if kind == "members":
                    if not self._joined.is_set():
                        self._joined.set()
                    raw = msg.get("cooldown")
                    if isinstance(raw, int):
                        with self.lock:
                            self.cooldown = max(0, min(120, raw))
                        self.events.put(("cooldown", self.cooldown))
                    role = msg.get("role")
                    if role in ROLE_LABEL and role != self.role:
                        previous = self.role
                        self.role = role
                        if role == "chef":
                            self.events.put(("log", "Tu es maintenant chef : tu peux exclure, promouvoir et régler le délai"))
                        elif previous == "chef":
                            self.events.put(("log", "Tu n'es plus chef"))
                    self._apply_roster(msg.get("members") or [])
                elif kind == "cmd":
                    self.events.put(("cmd", msg))
                elif kind == "wait":
                    try:
                        left = max(1, int(msg.get("left", 1)))
                    except (TypeError, ValueError):
                        left = 1
                    with self.lock:
                        self.blocked_until[self.id] = time.time() + left
                    self.events.put(("status", f"Attends {left} s avant la prochaine farce"))
                elif kind == "kicked":
                    self.events.put(("status", "Tu as été exclu du salon"))
                    self.events.put(("log", "Un chef t'a exclu"))
                    break
                elif kind == "closed":
                    self.events.put(("status", "L'hôte a fermé le salon"))
                    self.events.put(("log", f"{msg.get('name', 'L\'hôte')} a quitté : le salon est fermé"))
                    break
        except OSError:
            if self.link is link:
                self.events.put(("status", "Connexion à l'hôte perdue"))
        if self.link is not link:
            return
        self.link = None
        with self.lock:
            self.members.clear()
        self.role = None
        self.events.put(("members", None))

    def _admin(self, actor, action, target):
        with self.lock:
            actor_role = "hote" if actor == self.id else self.members.get(actor, {}).get("role")
            target_info = None if target == self.id else self.members.get(target)
        if actor_role not in ("hote", "chef") or target_info is None:
            return
        if action == "kick":
            if target_info["role"] == "hote" or (actor_role == "chef" and target_info["role"] == "chef"):
                return
            with self.lock:
                link = self.clients.get(target)
                self.banned.add(target_info["name"].casefold())
            if link:
                self._send(link, {"type": "kicked", "id": self.id, "name": self.name, "reason": "exclu"})
                link.sock.close()
            self.events.put(("log", f"{target_info['name']} a été exclu"))
        elif action == "promote" and target_info["role"] == "membre":
            with self.lock:
                if target in self.members:
                    self.members[target]["role"] = "chef"
            self.events.put(("log", f"{target_info['name']} est maintenant chef"))
            self._broadcast_members()
        elif action == "demote" and actor_role == "hote" and target_info["role"] == "chef":
            with self.lock:
                if target in self.members:
                    self.members[target]["role"] = "membre"
            self.events.put(("log", f"{target_info['name']} n'est plus chef"))
            self._broadcast_members()

    def _set_cooldown(self, actor, seconds):
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return
        seconds = max(0, min(120, seconds))
        with self.lock:
            actor_role = "hote" if actor == self.id else self.members.get(actor, {}).get("role")
            if actor_role not in ("hote", "chef"):
                return
            self.cooldown = seconds
        text = "aucun" if seconds == 0 else f"{seconds} s"
        self.events.put(("log", f"Délai entre deux farces : {text}"))
        self.events.put(("cooldown", seconds))
        self._broadcast_members()

    def _mark_send(self, member):
        """0 si la farce peut partir, sinon les secondes qu'il reste à attendre."""
        with self.lock:
            now = time.time()
            wait = self.cooldown
            left = max(self.blocked_until.get(member, 0) - now,
                       wait - (now - self.last_cmd.get(member, 0)) if wait > 0 else 0)
            if left > 0:
                return left
            self.last_cmd[member] = now
            self.blocked_until.pop(member, None)
            return 0

    def _broadcast_members(self):
        with self.lock:
            clients = list(self.clients)
            cooldown = self.cooldown
            roster = [{"id": self.id, "name": self.name, "role": "hote"}] + [
                {"id": mid, "name": m["name"], "role": m["role"]} for mid, m in self.members.items()]
        self.events.put(("members", None))
        for mid in clients:
            with self.lock:
                link = self.clients.get(mid)
            if link:
                with self.lock:
                    role = self.members.get(mid, {}).get("role", "membre")
                visible = [person for person in roster if person["id"] != mid]
                self._send(link, {"type": "members", "members": visible, "role": role,
                                  "cooldown": cooldown, "v": PROTOCOL, "id": self.id, "name": self.name})

    def _apply_roster(self, roster):
        with self.lock:
            self.members = {m["id"]: {"name": str(m["name"])[:32], "role": m.get("role", "membre")}
                            for m in roster if isinstance(m, dict) and m.get("id") and m["id"] != self.id}
        self.events.put(("members", None))

    def _clean_cmd(self, msg):
        cmd = msg.get("cmd")
        if not isinstance(cmd, str) or not cmd.isidentifier():
            return None
        targets = msg.get("targets")
        if targets is not None:
            if not isinstance(targets, list):
                return None
            targets = [str(t)[:16] for t in targets[:32]]
        return {"type": "cmd", "cmd": cmd[:32], "args": msg.get("args") if isinstance(msg.get("args"), dict) else {},
                "label": str(msg.get("label") or cmd)[:48], "targets": targets,
                "id": str(msg.get("id", ""))[:16], "name": str(msg.get("name") or "?")[:32]}

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
        self.room_var = tk.StringVar(value=code_only(config.get("room", "")))
        self.status_var = tk.StringVar(value="Choisis un pseudo, puis rejoins ou crée un salon")
        self.received_var = tk.StringVar(value="Dernière farce reçue : —")
        self.include_me = tk.BooleanVar(value=False)
        self.blocked = tk.BooleanVar(value=False)
        self.volume_var = tk.IntVar(value=config.get("volume", 80))
        self.cooldown_var = tk.StringVar(value="Délai entre farces : aucun")
        self._hosting = bool(config.get("host"))
        self._guest = bool(config.get("guest"))
        self.peer = Peer(self.events)
        self.effects = effects.Effects(root, self.received_var.set)
        self.effects.set_volume(self.volume_var.get())
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self._poll()
        if config.get("host"):
            root.after(200, self.resume_host)
        elif config.get("guest") and code_only(self.room_var.get()):
            root.after(200, self.join)

    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def join(self):
        room = normalize_room(self.room_var.get().partition("@")[0])
        name = self.name_var.get().strip()[:32]
        if not name:
            self.status_var.set("Choisis d'abord un pseudo")
            return
        if room is None:
            self.status_var.set("Code de salon invalide (crée-en un avec « Nouveau salon »)")
            return
        self.room_var.set(format_room(room))
        self._hosting = False
        self._guest = True
        self.save_config()
        self.status_var.set("Recherche de l'hôte…")
        self.root.update_idletasks()
        addresses = find_host(room)
        if not addresses:
            self.status_var.set("Hôte introuvable. Il doit laisser PrankBox ouvert.")
            return
        if self.peer.connect(room, name, addresses):
            self.log(f"Tu rejoins le salon en tant que {name}")

    def save_config(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"room": code_only(self.room_var.get()), "host": getattr(self, "_hosting", False),
                       "guest": getattr(self, "_guest", False),
                       "name": self.name_var.get().strip()[:32], "volume": self.volume_var.get()}, f)

    def on_volume(self, value):
        self.effects.set_volume(int(float(value)))

    def open_addons(self):
        """Ouvre le dossier où l'on dépose pieds, poro, screamers et sons Discord."""
        os.startfile(effects.addons_dir())

    def test_volume(self):
        self.save_config()
        effects.play_sound("discord_ping")

    def new_room(self):
        name = self.name_var.get().strip()[:32]
        if not name:
            self.status_var.set("Choisis d'abord un pseudo")
            return
        self.status_var.set("Création du salon sur ton PC…")
        self.root.update_idletasks()
        self._open_host(new_room_code(), name)
        self.log(f"Salon créé : tu es l'hôte et un chef ({name})")

    def resume_host(self):
        code = normalize_room(self.room_var.get().partition("@")[0])
        name = self.name_var.get().strip()[:32]
        if code and name:
            self._open_host(code, name)
            self.log("Salon rouvert sur ton PC, avec le même code")

    def _open_host(self, code, name):
        self.peer.host(code, name)
        self.room_var.set(format_room(code))
        self._hosting = True
        self._guest = False
        self.save_config()

    def exclude_selected(self):
        self._admin_selected("kick", "exclure")

    def promote_selected(self):
        self._admin_selected("promote", "promouvoir")

    def demote_selected(self):
        if self.peer.role != "hote":
            self.status_var.set("Seul l'hôte peut retirer un chef")
            return
        targets = self.selected_targets()
        roles = {mid: role for mid, _name, role in self.peer.snapshot()}
        chefs = [target for target in (targets or ()) if roles.get(target) == "chef"]
        if not chefs:
            self.status_var.set("Sélectionne un chef à retirer")
            return
        for target in chefs:
            self.peer.admin("demote", target)

    def apply_cooldown(self):
        if self.peer.role not in ("hote", "chef"):
            self.status_var.set("Seul un chef peut régler le délai")
            return
        try:
            seconds = int(self.cooldown_spin.get())
        except ValueError:
            self.status_var.set("Le délai doit être un nombre de secondes")
            return
        self.peer.set_cooldown(max(0, min(120, seconds)))

    def _admin_selected(self, action, verb):
        if self.peer.role not in ("hote", "chef"):
            self.status_var.set("Seul un chef peut faire ça")
            return
        targets = self.selected_targets()
        if not targets:
            self.status_var.set(f"Sélectionne qui {verb}")
            return
        for target in targets:
            self.peer.admin(action, target)

    def toggle_room_visibility(self):
        hidden = self.room_entry.cget("show") == ""
        self.room_entry.configure(show="•" if hidden else "")
        self.reveal_button.configure(text="Afficher" if hidden else "Masquer")
        if not hidden:
            self.root.after(10000, lambda: self.room_entry.cget("show") or self.toggle_room_visibility())

    def copy_room(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(code_only(self.room_var.get()))
        self.log("Code copié. Il ne contient pas l'adresse de ton PC.")

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
        root.geometry("1100x760")
        root.minsize(420, 520)
        self._flows = []
        self._cols = 3
        self._wide = True
        self._active = "salon"

        self.sidebar = tk.Frame(root, bg=SIDEBAR, width=232)
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=16, pady=(18, 8))
        tk.Label(brand, text="PrankBox", bg=SIDEBAR, fg="white",
                 font=("Segoe UI Black", 18)).pack(anchor="w")
        tk.Label(brand, text="Trollez-vous entre potes", bg=SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(brand, text=f"Version {APP_VERSION}", bg=SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        self.nav_box = tk.Frame(self.sidebar, bg=SIDEBAR)
        self.nav_box.pack(fill="both", expand=True, padx=10, pady=8)
        self.nav_buttons = {}
        for key, icon, title in PAGES:
            btn = tk.Button(self.nav_box, text=f"  {icon}   {title}", command=lambda k=key: self._show(k),
                            anchor="w", relief="flat", bd=0, cursor="hand2", bg=SIDEBAR, fg="white",
                            activebackground=SIDEBAR_HOVER, activeforeground="white",
                            font=("Segoe UI", 11, "bold"), padx=10, pady=8)
            btn.pack(fill="x", pady=3)
            btn.bind("<Enter>", lambda e, b=btn, k=key: b.configure(
                bg=ORANGE if self._active == k else SIDEBAR_HOVER,
                fg=BTN_TEXT if self._active == k else "white"))
            btn.bind("<Leave>", lambda e, b=btn, k=key: self._paint_nav(b, k))
            self.nav_buttons[key] = btn

        self.main = tk.Frame(root, bg=BG)
        canvas = tk.Canvas(self.main, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.main, orient="vertical", command=canvas.yview)
        self.body = tk.Frame(canvas, bg=BG)
        body_id = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.canvas = canvas

        self.pages = {}
        builders = {
            "salon": self._build_salon_card, "ecran": self._build_screen_card,
            "jeux": self._build_game_card, "chaos": self._build_chaos_card, "fun": self._build_fun_card,
            "discord": lambda: self._button_grid(self._card("Discord & sons"), DISCORD_EFFECTS),
            "plus": lambda: self._button_grid(self._card("Encore plus de farces"), MORE_EFFECTS),
            "journal": self._build_log_card,
        }
        for key, _icon, _title in PAGES:
            page = tk.Frame(self.body, bg=BG)
            self.pages[key] = page
            self.page = page
            tk.Frame(page, bg=BG, height=8).pack(fill="x")
            builders[key]()
        self._pack_shell(True)
        self._show("salon")
        root.bind("<Configure>", self._on_root_resize)

    def _paint_nav(self, btn, key):
        on = self._active == key
        btn.configure(bg=ORANGE if on else SIDEBAR, fg=BTN_TEXT if on else "white",
                      activebackground=ORANGE if on else SIDEBAR_HOVER,
                      activeforeground=BTN_TEXT if on else "white")

    def _show(self, key):
        self._active = key
        for name, page in self.pages.items():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for name, btn in self.nav_buttons.items():
            self._paint_nav(btn, name)
        self.canvas.yview_moveto(0)

    def _pack_shell(self, wide):
        self.sidebar.pack_forget()
        self.main.pack_forget()
        if wide:
            self.sidebar.pack_propagate(False)
            self.sidebar.configure(width=232)
            self.sidebar.pack(side="left", fill="y")
            self.main.pack(side="left", fill="both", expand=True)
        else:
            self.sidebar.pack_propagate(True)
            self.sidebar.pack(side="top", fill="x")
            self.main.pack(side="top", fill="both", expand=True)
        for key, icon, title in PAGES:
            btn = self.nav_buttons[key]
            btn.pack_forget()
            btn.configure(text=f"  {icon}   {title}" if wide else icon, anchor="w" if wide else "center",
                          padx=10 if wide else 6)
            btn.pack(fill="x", pady=3) if wide else btn.pack(side="left", padx=2, pady=6)

    def _on_root_resize(self, event):
        if event.widget is not self.root or event.width < 100:
            return
        wide = event.width >= 860
        cols = 3 if event.width >= (1080 if wide else 760) else 2 if event.width >= 560 else 1
        if wide != self._wide:
            self._wide = wide
            self._pack_shell(wide)
        if cols != self._cols:
            self._cols = cols
            for parent, buttons, row in self._flows:
                self._place_buttons(parent, buttons, row, cols)
        if getattr(self, "status_label", None):
            self.status_label.configure(wraplength=max(200, event.width - (280 if wide else 48)))

    def _card(self, title):
        outer = tk.Frame(self.page, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        outer.pack(fill="x", padx=12, pady=7)
        head = tk.Frame(outer, bg=CARD)
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Frame(head, bg=ORANGE, width=4, height=22).pack(side="left", padx=(0, 10))
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=("Segoe UI", 13, "bold")).pack(side="left")
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="x", padx=10, pady=(0, 12))
        for col in range(6):
            inner.columnconfigure(col, weight=1)
        return inner

    def _button_grid(self, parent, effects, row=0):
        holder = tk.Frame(parent, bg=CARD)
        holder.grid(row=row, column=0, columnspan=6, sticky="ew")
        buttons = [make_button(holder, label, lambda c=cmd: self.trigger(c)) for label, cmd in effects]
        self._flows.append((holder, buttons, 0))
        self._place_buttons(holder, buttons, 0, self._cols)
        return row + 1

    def _place_buttons(self, parent, buttons, row, cols):
        for col in range(6):
            parent.columnconfigure(col, weight=1 if col < cols else 0, uniform="boutons" if col < cols else "")
        for idx, btn in enumerate(buttons):
            r, col = divmod(idx, cols)
            btn.grid(row=row + r, column=col, sticky="ew", padx=4, pady=4)

    def _hint(self, parent, text, row):
        tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=("Segoe UI", 9), wraplength=420,
                 justify="left").grid(row=row, column=0, columnspan=6, sticky="ew", padx=4, pady=(8, 2))

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
                    fg=TEXT, size=12).grid(row=0, column=0, columnspan=6, sticky="ew", padx=4, pady=(0, 6))
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
        self.log_box.grid(row=0, column=0, columnspan=6, sticky="ew", padx=4, pady=4)

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
        self.status_label = tk.Label(card, textvariable=self.status_var, bg=CARD, fg=TEXT,
                                     font=("Segoe UI", 9, "bold"), wraplength=640, justify="left")
        self.status_label.grid(row=3, column=0, columnspan=6, sticky="ew", padx=4)

        self.count_var = tk.StringVar(value="Personne d'autre dans le salon")
        self.you_var = tk.StringVar(value="Toi  ·  pas dans un salon")
        tk.Label(card, textvariable=self.you_var, bg=CARD, fg=ORANGE,
                 font=("Segoe UI Emoji", 10, "bold")).grid(row=4, column=0, columnspan=6, sticky="w", padx=4,
                                                           pady=(8, 0))
        tk.Label(card, textvariable=self.count_var, bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).grid(row=5, column=0, columnspan=6, sticky="w", padx=4)
        self.members_box = tk.Listbox(card, height=6, selectmode="multiple", exportselection=False,
                                      bg=INPUT_BG, fg=TEXT, selectbackground=ORANGE,
                                      selectforeground=BTN_TEXT, relief="flat", highlightthickness=0,
                                      font=("Segoe UI Emoji", 12), activestyle="none")
        self.members_box.grid(row=6, column=0, columnspan=6, sticky="ew", padx=4, pady=4)
        tk.Label(card, text="👑 = chef. Aucune sélection = tout le monde sauf toi.",
                 bg=CARD, fg=MUTED, font=("Segoe UI Emoji", 8), justify="left").grid(
            row=7, column=0, columnspan=6, sticky="w", padx=4)
        tk.Label(card, textvariable=self.cooldown_var, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=8, column=0, columnspan=3, sticky="w", padx=4, pady=(8, 0))
        self.cooldown_spin = tk.Spinbox(card, from_=0, to=120, width=5, bg=INPUT_BG, fg=TEXT,
                                        buttonbackground=DARK_BTN, insertbackground=TEXT, relief="flat",
                                        font=("Segoe UI", 11))
        self.cooldown_spin.grid(row=8, column=3, sticky="ew", padx=4, pady=(8, 0), ipady=2)
        make_button(card, "Appliquer", self.apply_cooldown, bg=DARK_BTN, hover=DARK_BTN_HOVER,
                    fg=TEXT, size=9).grid(row=8, column=4, columnspan=2, sticky="ew", padx=4, pady=(8, 0))
        make_button(card, "Exclure", self.exclude_selected, bg=RED, hover=RED_HOVER, fg=TEXT, size=9).grid(
            row=9, column=0, sticky="ew", padx=4, pady=4)
        make_button(card, "Promouvoir chef", self.promote_selected, bg=DARK_BTN, hover=DARK_BTN_HOVER,
                    fg=TEXT, size=9).grid(row=9, column=1, columnspan=3, sticky="ew", padx=4, pady=4)
        make_button(card, "Retirer chef", self.demote_selected, bg=DARK_BTN, hover=DARK_BTN_HOVER,
                    fg=TEXT, size=9).grid(row=9, column=4, columnspan=2, sticky="ew", padx=4, pady=4)
        for row, (text, var) in enumerate((("M'inclure aussi (pour tester)", self.include_me),
                                           ("Bloquer les farces qu'on m'envoie", self.blocked)), start=10):
            tk.Checkbutton(card, text=text, variable=var, bg=CARD, fg=TEXT, selectcolor=INPUT_BG,
                           activebackground=CARD, activeforeground=TEXT, font=("Segoe UI", 9)).grid(
                row=row, column=0, columnspan=6, sticky="w")
        tk.Label(card, text="Volume des farces reçues (0 = muet)", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=12, column=0, columnspan=6, sticky="w", padx=4, pady=(8, 0))
        tk.Scale(card, from_=0, to=100, orient="horizontal", variable=self.volume_var, command=self.on_volume,
                 bg=CARD, fg=TEXT, troughcolor=INPUT_BG, activebackground=ORANGE, highlightthickness=0,
                 bd=0, sliderrelief="flat", font=("Segoe UI", 8)).grid(
            row=13, column=0, columnspan=5, sticky="ew", padx=4)
        make_button(card, "Tester", self.test_volume, bg=DARK_BTN, hover=DARK_BTN_HOVER, fg=TEXT,
                    size=9).grid(row=13, column=5, sticky="ew", padx=4, pady=(4, 0))
        tk.Label(card, textvariable=self.received_var, bg=CARD, fg=ORANGE, font=("Segoe UI", 9, "bold"),
                 wraplength=640, justify="left").grid(row=14, column=0, columnspan=6, sticky="ew", padx=4,
                                                      pady=(4, 0))
        make_button(card, "STOP — annuler les effets chez tout le monde", self.stop_all,
                    bg=RED, hover=RED_HOVER, fg=TEXT).grid(
            row=15, column=0, columnspan=6, sticky="ew", padx=4, pady=(8, 0))
        make_button(card, "Ajouter des images et des sons", self.open_addons,
                    bg=DARK_BTN, hover=DARK_BTN_HOVER, fg=TEXT, size=9).grid(
            row=16, column=0, columnspan=6, sticky="ew", padx=4, pady=(8, 0))

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
        if count is None:
            return
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
        me = self.peer.role
        self.you_var.set("Toi  ·  pas dans un salon" if not me else
                         f"Toi   👑  {ROLE_LABEL[me]}" if me in ("hote", "chef") else "Toi  ·  membre")
        for mid, name, role in members:
            mark = "👑  " if role in ("hote", "chef") else "    "
            suffix = "   · hôte" if role == "hote" else ""
            self.members_box.insert("end", f"  {mark}{name}{suffix}")
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
                elif kind == "cooldown":
                    self.cooldown_var.set("Délai entre farces : aucun" if payload == 0
                                          else f"Délai entre farces : {payload} s")
                    self.cooldown_spin.delete(0, "end")
                    self.cooldown_spin.insert(0, str(payload))
        except queue.Empty:
            pass
        self.root.after(50, self._poll)


def _windows_status():
    """Affiche une petite fenêtre seulement pendant le téléchargement du .exe."""
    box = {"root": None, "label": None}

    def status(text):
        if box["root"] is None:
            root = tk.Tk()
            root.title("PrankBox")
            root.configure(bg=BG)
            root.resizable(False, False)
            label = tk.Label(root, text=text, bg=BG, fg=TEXT, font=("Segoe UI", 12), padx=28, pady=32)
            label.pack()
            root.update_idletasks()
            width, height = root.winfo_width(), root.winfo_height()
            root.geometry(f"+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 2}")
            box["root"], box["label"] = root, label
        else:
            box["label"].configure(text=text)
        box["root"].update()

    def close(hold_ms=0):
        root = box["root"]
        if root is None:
            return
        box["root"] = None
        if hold_ms:
            root.after(hold_ms, root.destroy)
            root.mainloop()
        else:
            root.destroy()

    return status, close


def main():
    frozen = getattr(sys, "frozen", False)
    if not frozen and os.environ.get("PRANKBOX_NO_UPDATE") != "1" and updater.check():
        # Relance avec les nouveaux fichiers ; la variable évite une boucle si GitHub se contredit.
        # Le .exe s'installe au tour suivant, même avec cette variable : l'ancien programme ne la connaît pas.
        env = {**os.environ, "PRANKBOX_NO_UPDATE": "1"}
        subprocess.Popen([sys.executable, os.path.abspath(__file__), *sys.argv[1:]], env=env)
        return
    # PRANKBOX_SOURCE=1 garde le programme Python, pour travailler dessus.
    if os.environ.get("PRANKBOX_SOURCE") != "1":
        script_dir = os.path.dirname(sys.executable if frozen else os.path.abspath(__file__))
        plan = updater.windows_plan(frozen, APP_RELEASE)
        if plan is not None:
            status, close = _windows_status()
            installed = False
            try:
                installed = bool(updater.apply_windows(plan, frozen, script_dir, status))
            finally:
                close(0 if installed else 4000)
            if installed:
                return
    try:
        root = tk.Tk()
        PrankBoxApp(root)
        root.mainloop()
    except Exception:
        if frozen:
            with open(os.path.join(_user_dir(), "erreur.log"), "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    main()
