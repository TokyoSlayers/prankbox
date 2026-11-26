"""Construit le programme Windows public de PrankBox.

Les images et les sons de tiers (poro, OIIA) ne sont pas inclus : ce dossier
est publié sur GitHub. Usage interne : tools/publier.py appelle package().
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP_NAME = "PrankBox-windows.zip"


def package(dest_root=None):
    """Construit PrankBox.exe et le zip signé ensuite par publier.py.

    Renvoie (chemin du zip, empreinte SHA-256).
    """
    source = os.path.join(ROOT, "prankbox.py")
    with open(source, encoding="utf-8") as f:
        if not re.search(r"(?m)^APP_RELEASE = [1-9]\d*", f.read()):
            raise SystemExit("APP_RELEASE doit être écrit avant de construire le programme public.")

    dest_root = dest_root or os.path.join(ROOT, "dist", "release")
    if os.path.isdir(dest_root):
        shutil.rmtree(dest_root)
    os.makedirs(dest_root)
    images = _public_images()
    try:
        return _package(dest_root, images, source)
    finally:
        if images is not None:
            shutil.rmtree(images, ignore_errors=True)


def _package(dest_root, images, source):
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", "PrankBox",
        "--distpath", dest_root,
        "--workpath", os.path.join(ROOT, "build", "release"),
        "--specpath", os.path.join(ROOT, "build", "release"),
    ]
    if images is not None:
        cmd.append(f"--add-data={images}{os.pathsep}images")
    cmd.append(source)
    subprocess.run(cmd, cwd=ROOT, check=True)

    folder = os.path.join(dest_root, "PrankBox")
    zip_path = os.path.join(dest_root, ZIP_NAME)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for dirpath, _dirnames, filenames in os.walk(folder):
            for name in filenames:
                full = os.path.join(dirpath, name)
                archive.write(full, os.path.relpath(full, folder).replace("\\", "/"))
    with zipfile.ZipFile(zip_path) as archive:
        banned = [name for name in archive.namelist() if _is_third_party(name)]
    if banned:
        raise SystemExit("Le zip public contient des fichiers de tiers.")
    with open(zip_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return zip_path, digest


def _is_third_party(name):
    parts = name.lower().replace("\\", "/").split("/")
    return "sounds" in parts or any(part == "poro" or part.startswith("poro_") or "oiia" in part for part in parts)


def _public_images():
    """Copie les images du dépôt, pas les dossiers de tiers."""
    src = os.path.join(ROOT, "images")
    if not os.path.isdir(src):
        return None
    dest = tempfile.mkdtemp(prefix="prankbox-images-")
    found = False
    for name in os.listdir(src):
        path = os.path.join(src, name)
        if os.path.isfile(path):
            shutil.copy2(path, os.path.join(dest, name))
            found = True
    return dest if found else None


if __name__ == "__main__":
    print(package()[0])
