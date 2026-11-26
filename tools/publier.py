"""Publie une nouvelle version de PrankBox : signe le manifeste puis pousse sur GitHub.

Usage : python tools/publier.py "ce qui a changé"

La clé privée reste dans %APPDATA%\\PrankBox\\cle_privee_mises_a_jour.txt, hors du
dépôt. Sans elle personne ne peut publier une mise à jour acceptée par PrankBox :
garde-en une copie, si tu la perds il faudra redonner les fichiers à la main.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_windows  # noqa: E402
import signature  # noqa: E402
import updater  # noqa: E402

KEY_PATH = os.path.join(os.environ.get("APPDATA", ""), "PrankBox", "cle_privee_mises_a_jour.txt")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def _public_label(text):
    """9 devient 9.1, puis 9.2. Le numéro interne, lui, reste un entier."""
    match = re.search(r'(?m)^APP_VERSION = "(\d+(?:\.\d+)*)"', text)
    if not match:
        sys.exit("APP_VERSION introuvable dans prankbox.py : publication annulée.")
    label = match.group(1)
    if "." not in label:
        return f"{label}.1"
    head, patch = label.rsplit(".", 1)
    return f"{head}.{int(patch) + 1}"


def _set_release(version):
    path = os.path.join(ROOT, "prankbox.py")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    label = _public_label(text)
    new, count = re.subn(r"(?m)^APP_RELEASE = \d+", f"APP_RELEASE = {version}", text, count=1)
    new, count_label = re.subn(r'(?m)^APP_VERSION = "[^"]+"', f'APP_VERSION = "{label}"', new, count=1)
    if count != 1 or count_label != 1:
        sys.exit("APP_RELEASE ou APP_VERSION introuvable dans prankbox.py : publication annulée.")
    new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    return label


def _write_windows(version, digest, tag=None):
    info = {
        "sha256": digest,
        "url": f"https://github.com/{updater.REPO}/releases/download/v{tag or version}/PrankBox-windows.zip",
        "version": version,
    }
    with open(os.path.join(ROOT, "windows.json"), "wb") as f:
        f.write(json.dumps(info, indent=1, sort_keys=True).encode() + b"\n")


def load_secret():
    try:
        with open(KEY_PATH) as f:
            secret = bytes.fromhex(f.read().strip())
    except (OSError, ValueError):
        sys.exit(f"Clé privée introuvable ou illisible : {KEY_PATH}")
    if signature.public_key(secret) != updater.PUBLIC_KEY:
        sys.exit("Cette clé privée ne correspond pas à PUBLIC_KEY dans updater.py : publication annulée.")
    return secret


def publish_tree(secret, message, label=None):
    """Signe le manifeste et pousse. Le .exe signé est ajouté ensuite par GitHub Actions."""
    git("add", "-A")
    files = [f for f in git("ls-files", "-z").split("\0") if f and f not in updater.PROTECTED]
    if not git("status", "--porcelain") and os.path.exists(os.path.join(ROOT, updater.MANIFEST)):
        sys.exit("Rien de nouveau à publier.")

    manifest = {"version": updater.local_version() + 1, "files": {}}
    for rel in files:
        with open(os.path.join(ROOT, rel), "rb") as f:
            manifest["files"][rel] = hashlib.sha256(f.read()).hexdigest()
    data = json.dumps(manifest, indent=1, sort_keys=True).encode()
    with open(os.path.join(ROOT, updater.MANIFEST), "wb") as f:
        f.write(data)
    with open(os.path.join(ROOT, updater.MANIFEST_SIG), "w") as f:
        f.write(signature.sign(secret, data).hex())

    git("add", "-A")
    git("commit", "-q", "-m", f"v{label or manifest['version']} : {message}")
    git("push", "-q", "origin", updater.BRANCH)
    return manifest


def signpath_configured():
    """Vrai quand les accès SignPath sont sur le dépôt. Avant ça, l'exe part tel quel."""
    listed = subprocess.run(
        ["gh", "variable", "list", "--repo", updater.REPO],
        capture_output=True, text=True, check=False,
    )
    secrets = subprocess.run(
        ["gh", "secret", "list", "--repo", updater.REPO],
        capture_output=True, text=True, check=False,
    )
    text = listed.stdout + "\n" + secrets.stdout
    needed = ("SIGNPATH_ORGANIZATION_ID", "SIGNPATH_PROJECT_SLUG", "SIGNPATH_API_TOKEN")
    return all(name in text for name in needed)


def main():
    message = " ".join(sys.argv[1:]) or "Mise à jour"
    secret = load_secret()
    subprocess.run(["gh", "auth", "status"], check=True)

    version = updater.local_version() + 1
    label = _set_release(version)
    if signpath_configured():
        manifest = publish_tree(secret, message, label)
        subprocess.run(
            ["gh", "release", "create", f"v{label}",
             "--repo", updater.REPO, "--title", f"PrankBox {label}", "--notes", message],
            check=True,
        )
        print(f"Version {label} publiée. GitHub va faire signer PrankBox.exe.")
        print("Quand l'action Signer Windows est verte : python tools/finaliser_windows.py")
        return

    zip_path, digest = build_windows.package()
    _write_windows(version, digest, label)
    subprocess.run(
        ["gh", "release", "create", f"v{label}", zip_path,
         "--repo", updater.REPO, "--title", f"PrankBox {label}", "--notes", message],
        check=True,
    )
    publish_tree(secret, message, label)
    print(f"Version {label} publiée, avec le programme Windows.")
    print("SignPath n'est pas encore branché : l'exe part quand même, l'avertissement Windows reste.")
    print("Dès que les accès SignPath sont sur le dépôt, les prochaines versions repasseront par la signature.")


if __name__ == "__main__":
    main()
