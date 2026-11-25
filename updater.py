"""Mises à jour automatiques de PrankBox depuis GitHub, au lancement.

Le dépôt contient manifest.json (numéro de version + empreinte SHA-256 de
chaque fichier) et manifest.sig, sa signature Ed25519 faite avec la clé
privée de celui qui publie (tools/publier.py). Rien n'est installé si la
signature ne correspond pas à PUBLIC_KEY : modifier le dépôt ne suffit donc
pas pour faire tourner du code chez les autres.
"""

import hashlib
import json
import os
import urllib.parse
import urllib.request

import signature

REPO = "TokyoSlayers/prankbox"
BRANCH = "main"
PUBLIC_KEY = bytes.fromhex("00ba55c34a002bbcbe88119762b449d34c75bbf77889b5d50c66f65c394b16b5")
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST, MANIFEST_SIG = "manifest.json", "manifest.sig"
PROTECTED = {"prankbox_config.json", MANIFEST, MANIFEST_SIG}
TIMEOUT = 6


def _get(url, accept=None):
    request = urllib.request.Request(url, headers={"User-Agent": "PrankBox", **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def local_version():
    try:
        with open(os.path.join(HERE, MANIFEST), "rb") as f:
            return int(json.loads(f.read())["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _safe_path(rel):
    """Chemin absolu dans le dossier de PrankBox, ou None si le nom est louche."""
    if not isinstance(rel, str) or not rel or ":" in rel or rel.startswith(("/", "\\")):
        return None
    if ".." in rel.replace("\\", "/").split("/") or rel in PROTECTED:
        return None
    full = os.path.normpath(os.path.join(HERE, rel))
    return full if os.path.commonpath([full, HERE]) == HERE else None


def check(log=print):
    """Installe la dernière version signée. Renvoie True si des fichiers ont changé (il faut relancer)."""
    try:
        commit = _get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                      accept="application/vnd.github.sha").decode().strip()
    except OSError:
        commit = BRANCH  # API limitée (60 appels/h) : on lit la branche directement
    raw = f"https://raw.githubusercontent.com/{REPO}/{commit}/"
    try:
        manifest_bytes = _get(raw + MANIFEST)
        sig = bytes.fromhex(_get(raw + MANIFEST_SIG).decode().strip())
    except (OSError, ValueError) as exc:
        log(f"Mise à jour : GitHub injoignable ({exc}), on continue avec la version actuelle.")
        return False
    if not signature.verify(PUBLIC_KEY, manifest_bytes, sig):
        log("Mise à jour REFUSÉE : la signature ne correspond pas à la clé de PrankBox.")
        return False
    manifest = json.loads(manifest_bytes)
    version, files = int(manifest["version"]), manifest["files"]
    if version <= local_version():
        return False

    log(f"Mise à jour vers la version {version}…")
    pending = []
    for rel, digest in files.items():
        path = _safe_path(rel)
        if path is None:
            log(f"Mise à jour refusée : chemin interdit {rel!r}")
            return False
        if _sha256(path) == digest:
            continue
        try:
            data = _get(raw + urllib.parse.quote(rel))
        except OSError as exc:
            log(f"Mise à jour interrompue ({rel} : {exc}), on réessaiera au prochain lancement.")
            return False
        if hashlib.sha256(data).hexdigest() != digest:
            log(f"Mise à jour refusée : {rel} ne correspond pas à son empreinte signée.")
            return False
        pending.append((path, data))

    # Tout est téléchargé et vérifié avant d'écrire quoi que ce soit.
    for path, data in pending:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".maj", "wb") as f:
            f.write(data)
        os.replace(path + ".maj", path)
        log(f"  {os.path.relpath(path, HERE)}")
    for name, data in ((MANIFEST, manifest_bytes), (MANIFEST_SIG, sig.hex().encode())):
        with open(os.path.join(HERE, name), "wb") as f:
            f.write(data)
    log(f"PrankBox est à jour (version {version}).")
    return bool(pending)
