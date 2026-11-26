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
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile

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


def _remote_manifest(log=print):
    """Manifeste signé de la branche, ou None. Les messages sont ceux de l'ancienne mise à jour."""
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
        return None
    if not signature.verify(PUBLIC_KEY, manifest_bytes, sig):
        log("Mise à jour REFUSÉE : la signature ne correspond pas à la clé de PrankBox.")
        return None
    try:
        manifest = json.loads(manifest_bytes)
        return manifest_bytes, sig, int(manifest["version"]), manifest["files"], raw
    except (ValueError, KeyError, TypeError):
        log("Mise à jour refusée : manifeste illisible.")
        return None


def check(log=print):
    """Installe la dernière version signée. Renvoie True si des fichiers ont changé (il faut relancer)."""
    remote = _remote_manifest(log)
    if remote is None:
        return False
    manifest_bytes, sig, version, files, raw = remote
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


def _allowed_url(url):
    parts = urllib.parse.urlparse(url)
    prefix = f"/{REPO}/releases/download/"
    return parts.scheme == "https" and parts.netloc == "github.com" and parts.path.startswith(prefix)


def _appdata():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PrankBox")


def _install_dir(frozen, script_dir):
    """Dossier du .exe. Le dépôt git n'est pas écrasé : le programme va dans AppData."""
    if not frozen and os.path.isdir(os.path.join(script_dir, ".git")):
        base = os.environ.get("LOCALAPPDATA") or _appdata()
        return os.path.join(base, "PrankBox", "app")
    return script_dir


def _stamp_path():
    """Empreinte du .exe déjà installé. Elle reste dans AppData, pas à côté du programme."""
    return os.path.join(_appdata(), "release.txt")


def _stamp_ok(folder, digest):
    exe = os.path.join(folder, "PrankBox.exe")
    try:
        with open(_stamp_path(), encoding="utf-8") as f:
            return os.path.isfile(exe) and not os.path.isdir(os.path.join(folder, "_internal")) and f.read().strip() == digest
    except OSError:
        return False


def _write_stamp(digest):
    os.makedirs(_appdata(), exist_ok=True)
    with open(_stamp_path(), "w", encoding="utf-8") as f:
        f.write(digest)


def _drop_bundle(folder):
    """Enlève l'ancien dossier posé à côté du .exe."""
    internal = os.path.join(folder, "_internal")
    if os.path.isdir(internal):
        shutil.rmtree(internal, ignore_errors=True)
    leftover = os.path.join(folder, ".prankbox_release")
    if os.path.isfile(leftover):
        try:
            os.remove(leftover)
        except OSError:
            pass


def windows_plan(frozen, release, log=print):
    """Décrire le .exe à installer, ou None s'il n'y a rien à faire.

    L'ancienne version Python installe toujours le programme Windows.
    Le .exe ne le retélécharge que si le numéro signé est plus grand que le sien.
    """
    remote = _remote_manifest(log)
    if remote is None:
        return None
    _manifest_bytes, _sig, _version, files, raw = remote
    digest = files.get("windows.json") if isinstance(files, dict) else None
    if not isinstance(digest, str):
        return None
    try:
        data = _get(raw + urllib.parse.quote("windows.json"))
    except OSError as exc:
        log(f"Mise à jour : programme Windows injoignable ({exc}).")
        return None
    if hashlib.sha256(data).hexdigest() != digest:
        log("Mise à jour refusée : windows.json ne correspond pas à son empreinte signée.")
        return None
    try:
        info = json.loads(data)
        win_version, url, sha = int(info["version"]), info["url"], info["sha256"]
    except (ValueError, KeyError, TypeError):
        log("Mise à jour refusée : description du programme Windows illisible.")
        return None
    if not isinstance(url, str) or not _allowed_url(url) or not isinstance(sha, str) or len(sha) != 64:
        log("Mise à jour refusée : adresse du programme Windows invalide.")
        return None
    if win_version <= 0 or (frozen and (release <= 0 or win_version <= release)):
        return None
    return {"version": win_version, "url": url, "sha256": sha.lower()}


def _migrate_config(script_dir):
    """Garde le pseudo et le salon : le .exe lit ses réglages dans AppData."""
    src = os.path.join(script_dir, "prankbox_config.json")
    if not os.path.isfile(src):
        return
    folder = _appdata()
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "prankbox_config.json")
    if not os.path.isfile(dest):
        shutil.copyfile(src, dest)


def _extract(zip_path, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or ":" in name or ".." in name.split("/"):
                raise ValueError(f"archive refusée : {info.filename!r}")
        archive.extractall(dest)


def _launch(exe):
    subprocess.Popen([exe], cwd=os.path.dirname(exe))


def _remove_python(folder):
    """Efface les .py de l'ancienne installation. Ne touche pas à un dépôt git, ni au .exe."""
    if os.path.isdir(os.path.join(folder, ".git")):
        return
    caches = []
    for dirpath, dirnames, filenames in os.walk(folder):
        kept = []
        for name in dirnames:
            if name == "__pycache__":
                caches.append(os.path.join(dirpath, name))
            elif name not in (".git", "_internal"):
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name.endswith((".py", ".pyc")):
                try:
                    os.remove(os.path.join(dirpath, name))
                except OSError:
                    pass
    for cache in caches:
        shutil.rmtree(cache, ignore_errors=True)


def _schedule_replace(stage, dest, pid):
    """Remplace le .exe en cours après sa fermeture : Windows interdit de l'écraser avant."""
    maj = os.path.join(_appdata(), "maj")
    os.makedirs(maj, exist_ok=True)
    script = os.path.join(maj, "remplacer.ps1")

    def ps(path):
        return path.replace("'", "''")

    text = (
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue\r\n"
        "Start-Sleep -Seconds 1\r\n"
        f"Copy-Item -LiteralPath '{ps(os.path.join(stage, 'PrankBox.exe'))}' -Destination '{ps(os.path.join(dest, 'PrankBox.exe'))}' -Force\r\n"
        f"Remove-Item -LiteralPath '{ps(os.path.join(dest, '_internal'))}' -Recurse -Force -ErrorAction SilentlyContinue\r\n"
        f"Remove-Item -LiteralPath '{ps(os.path.join(dest, '.prankbox_release'))}' -Force -ErrorAction SilentlyContinue\r\n"
        f"Start-Process -FilePath '{ps(os.path.join(dest, 'PrankBox.exe'))}'\r\n"
        f"Remove-Item -LiteralPath '{ps(stage)}' -Recurse -Force -ErrorAction SilentlyContinue\r\n"
    )
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", script],
        cwd=maj, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _download(url, dest, on_status):
    request = urllib.request.Request(url, headers={"User-Agent": "PrankBox"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        got = 0
        with open(dest, "wb") as f:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total and on_status is not None:
                    on_status(f"Téléchargement de PrankBox… {got * 100 // total} %")


def apply_windows(plan, frozen, script_dir, on_status=None):
    """Installe et lance le .exe. Renvoie True si ce programme doit se fermer."""
    dest = _install_dir(frozen, script_dir)
    if not frozen and _stamp_ok(dest, plan["sha256"]):
        _migrate_config(script_dir)
        _launch(os.path.join(dest, "PrankBox.exe"))
        _remove_python(script_dir)
        return True

    if on_status is not None:
        on_status("Téléchargement de PrankBox…")
    folder = os.path.join(_appdata(), "maj")
    os.makedirs(folder, exist_ok=True)
    zip_path = os.path.join(folder, "PrankBox-windows.zip")
    try:
        _download(plan["url"], zip_path, on_status)
        with open(zip_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != plan["sha256"]:
            if on_status is not None:
                on_status("Mise à jour refusée : le programme ne correspond pas à la signature.")
            return False
        if on_status is not None:
            on_status("Installation de PrankBox…")
        if frozen:
            stage = os.path.join(folder, "next")
            if os.path.isdir(stage):
                shutil.rmtree(stage)
            _extract(zip_path, stage)
            _write_stamp(plan["sha256"])
            _schedule_replace(stage, dest, os.getpid())
            return True
        _extract(zip_path, dest)
        _drop_bundle(dest)
        _write_stamp(plan["sha256"])
        _migrate_config(script_dir)
        _launch(os.path.join(dest, "PrankBox.exe"))
        _remove_python(script_dir)
        return True
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        if on_status is not None:
            on_status(f"Mise à jour impossible ({exc}).")
        return False
    finally:
        if os.path.isfile(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass
