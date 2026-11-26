"""Enregistre l'empreinte du PrankBox.exe signé par SignPath.

À lancer quand l'action Signer Windows est verte :

    python tools/finaliser_windows.py

Le .exe signé est déjà sur la page de la version. Cette commande met à jour
l'empreinte que les autres vérifient avant de le télécharger.
"""

import hashlib
import os
import re
import sys
import urllib.request
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import publier  # noqa: E402
import updater  # noqa: E402


def _app_release():
    with open(os.path.join(ROOT, "prankbox.py"), encoding="utf-8") as handle:
        match = re.search(r"(?m)^APP_RELEASE = (\d+)", handle.read())
    if not match or int(match.group(1)) <= 0:
        sys.exit("APP_RELEASE introuvable.")
    return int(match.group(1))


def main():
    version = _app_release()
    url = f"https://github.com/{updater.REPO}/releases/download/v{version}/PrankBox-windows.zip"
    path = os.path.join(ROOT, "dist", "release", "PrankBox-windows.zip")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "PrankBox"}), timeout=120) as response:
            data = response.read()
    except OSError as exc:
        sys.exit(f"Zip signé introuvable ({exc}). Attends que l'action Signer Windows soit verte.")
    with open(path, "wb") as handle:
        handle.write(data)
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != ["PrankBox.exe"]:
            sys.exit("Le zip signé ne contient pas uniquement PrankBox.exe.")
    digest = hashlib.sha256(data).hexdigest()
    current = os.path.join(ROOT, "windows.json")
    if os.path.isfile(current) and digest in open(current, encoding="utf-8").read():
        print(f"L'empreinte de la version {version} est déjà publiée.")
        return
    secret = publier.load_secret()
    publier._write_windows(version, digest)
    publier.publish_tree(secret, f"Empreinte du programme Windows signé {version}")
    print(f"Empreinte publiée pour v{version}. Les autres téléchargeront le .exe signé.")


if __name__ == "__main__":
    main()
