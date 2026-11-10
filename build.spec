# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas, binaries, hiddenimports = [], [], []

# Packages whose assets (DLLs, configs, data files) must be bundled
for pkg in (
    "PySide6",
    "llama_cpp",
    "ctranslate2",
    "onnxruntime",
    "kokoro_onnx",
    "misaki",
    "faster_whisper",
    "sounddevice",
    "soundfile",
    "av",
    "tokenizers",
    "phonemizer",
    "espeakng_loader",
    "espeakng",
    "pycaw",
    "comtypes",
    "wmi",
    "uiautomation",
    "pytesseract",
    "ddgs",
    "pyloudnorm",
    "scipy",
    "numpy",
    "psutil",
    "groq",
    "httpx",
    "nvidia",  # CUDA runtime DLLs if pip-installed
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
        print(f"[spec] collected {pkg}: {len(d)} data, {len(b)} bin, {len(h)} hidden")
    except Exception as e:
        print(f"[spec] skip {pkg}: {e}")

# Local modules that must be present as source
hiddenimports += ["tasks", "toolkit", "agent"]

a = Analysis(
    ["karen.py"],
    pathex=[os.getcwd()],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PyQt5", "PyQt6", "PySide2",
        "IPython", "jupyter", "notebook",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Karen",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX breaks onnxruntime + llama_cpp — leave off
    console=False,      # no terminal window at all
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Karen",
)