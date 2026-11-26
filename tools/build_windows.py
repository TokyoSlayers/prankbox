"""Construit le programme Windows public de PrankBox.

Le résultat publié est un seul PrankBox.exe : images et moteur sont dedans.
Les images et les sons de tiers (poro, OIIA) ne sont pas inclus.
Usage interne : tools/publier.py appelle package().
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
    work = os.path.join(ROOT, "build", "release")
    os.makedirs(work, exist_ok=True)
    spec = os.path.join(work, "PrankBox.spec")
    with open(spec, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_spec_text(source, images))
    upx = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PrankBox", "upx", "upx-4.2.4-win64")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", dest_root,
        "--workpath", work,
    ]
    if os.path.isfile(os.path.join(upx, "upx.exe")):
        cmd.append(f"--upx-dir={upx}")
    cmd.append(spec)
    subprocess.run(cmd, cwd=ROOT, check=True)

    exe = os.path.join(dest_root, "PrankBox.exe")
    if not os.path.isfile(exe):
        raise SystemExit("PrankBox.exe introuvable après la construction.")
    zip_path = os.path.join(dest_root, ZIP_NAME)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, "PrankBox.exe")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    if names != ["PrankBox.exe"] or any(_is_third_party(name) for name in names):
        raise SystemExit("Le zip doit contenir uniquement PrankBox.exe.")
    with open(zip_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return zip_path, digest


def _spec_text(source, images):
    datas = [(images, "images")] if images else []
    return f"""# -*- mode: python ; coding: utf-8 -*-
import os

a = Analysis(
    [{source!r}],
    pathex=[],
    binaries=[],
    datas={datas!r},
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=["pydoc", "doctest", "unittest", "ensurepip", "venv", "lib2to3", "xmlrpc", "pydoc_data"],
    noarchive=False,
    optimize=2,
)

_ENCODINGS = {{"cp1252.enc", "iso8859-1.enc", "iso8859-15.enc", "ascii.enc"}}
_MSGS = {{"fr.msg", "en.msg", "en_us.msg", "en_gb.msg"}}

def _keep_data(item):
    name = item[0].replace("\\\\", "/")
    if name.startswith("_tcl_data/tzdata/"):
        return False
    if name.startswith("_tcl_data/encoding/"):
        return os.path.basename(name).lower() in _ENCODINGS
    if "/msgs/" in name:
        return os.path.basename(name).lower() in _MSGS
    if name.startswith("_tk_data/images/") or name.startswith("_tk_data/demos/") or "/demos/" in name:
        return False
    if "tcltest" in name.lower():
        return False
    return True

def _keep_bin(item):
    base = os.path.basename(item[0]).lower()
    return base != "ucrtbase.dll" and not base.startswith("api-ms-win-")

a.datas = [item for item in a.datas if _keep_data(item)]
a.binaries = [item for item in a.binaries if _keep_bin(item)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PrankBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"""


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
