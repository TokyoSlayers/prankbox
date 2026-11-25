"""Publie une nouvelle version de PrankBox : signe le manifeste puis pousse sur GitHub.

Usage : python tools/publier.py "ce qui a changé"

La clé privée reste dans %APPDATA%\\PrankBox\\cle_privee_mises_a_jour.txt, hors du
dépôt. Sans elle personne ne peut publier une mise à jour acceptée par PrankBox :
garde-en une copie, si tu la perds il faudra redonner les fichiers à la main.
"""

import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
import signature  # noqa: E402
import updater  # noqa: E402

KEY_PATH = os.path.join(os.environ.get("APPDATA", ""), "PrankBox", "cle_privee_mises_a_jour.txt")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def main():
    message = " ".join(sys.argv[1:]) or "Mise à jour"
    try:
        with open(KEY_PATH) as f:
            secret = bytes.fromhex(f.read().strip())
    except (OSError, ValueError):
        sys.exit(f"Clé privée introuvable ou illisible : {KEY_PATH}")
    if signature.public_key(secret) != updater.PUBLIC_KEY:
        sys.exit("Cette clé privée ne correspond pas à PUBLIC_KEY dans updater.py : publication annulée.")

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
    git("commit", "-q", "-m", f"v{manifest['version']} : {message}")
    git("push", "-q", "origin", updater.BRANCH)
    print(f"Version {manifest['version']} publiée ({len(files)} fichiers signés). "
          "Tes potes la recevront au prochain lancement de PrankBox.")


if __name__ == "__main__":
    main()
