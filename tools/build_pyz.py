"""Build dist/padthru-linux.pyz: the Linux PC app as one file.

The result runs on any Linux with Python 3.8 or newer and needs no packages:
it contains Padthru's own code plus the pure-Python build of `websockets`.

    python3 tools/build_pyz.py [--out dist/padthru-linux.pyz] [--wheel path/to/websockets-12.0-py3-none-any.whl]

Works on Linux and Windows (the release build runs it on Windows).
"""
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipapp
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHEEL_NAME = "websockets-12.0-py3-none-any.whl"
WHEEL_URL = ("https://files.pythonhosted.org/packages/79/4d/9cc401e7b07e80532ebc8c8e993f42541534da9e9249c59ee0139dcb0352/"
             + WHEEL_NAME)
WHEEL_SHA256 = "dc284bbc8d7c78a6c69e0c7325ab46ee5e40bb4d50e494d8131a07ef47500e9e"

MAIN = '''\
"""Padthru for Linux: https://github.com/labanmne/padthru"""
import sys

import padthru

sys.exit(padthru.main())
'''


def get_wheel(path, cache_dir):
    if not path:
        path = os.path.join(cache_dir, WHEEL_NAME)
        if not os.path.exists(path):
            os.makedirs(cache_dir, exist_ok=True)
            print("downloading", WHEEL_URL)
            with urllib.request.urlopen(WHEEL_URL, timeout=60) as r, open(path + ".part", "wb") as f:
                shutil.copyfileobj(r, f)
            os.replace(path + ".part", path)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != WHEEL_SHA256:
        sys.exit(f"{path}: SHA-256 is {digest}, expected {WHEEL_SHA256}")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "padthru-linux.pyz"))
    ap.add_argument("--wheel", help=f"use this local {WHEEL_NAME} instead of downloading it")
    args = ap.parse_args()

    wheel = get_wheel(args.wheel, os.path.join(ROOT, "build", "cache"))
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("padthru.py", "vpad_linux.py"):
            shutil.copy(os.path.join(ROOT, "pc", name), tmp)
        with open(os.path.join(tmp, "__main__.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(MAIN)
        os.makedirs(os.path.join(tmp, "licenses"))
        shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(tmp, "licenses", "padthru.txt"))
        with zipfile.ZipFile(wheel) as z:
            for item in z.namelist():
                if item.startswith("websockets/") and item.endswith(".py"):
                    z.extract(item, tmp)
                elif item.endswith(".dist-info/LICENSE"):
                    with open(os.path.join(tmp, "licenses", "websockets.txt"), "wb") as f:
                        f.write(z.read(item))
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        zipapp.create_archive(tmp, args.out, interpreter="/usr/bin/env python3", compressed=True)
    print("built", args.out, os.path.getsize(args.out), "bytes")


if __name__ == "__main__":
    main()
