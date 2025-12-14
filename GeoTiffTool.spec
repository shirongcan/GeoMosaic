# -*- mode: python ; coding: utf-8 -*-
#
# Build: onedir (非单文件) Windows EXE
# - 把 GDAL( osgeo ) 的 DLL / data(gdal+proj) 一起打包
# - 入口脚本：geotiff_tool.py
#

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


# NOTE:
# PyInstaller executes spec files with a custom globals dict where __file__
# might not be defined (observed on some Windows setups). SPECPATH is provided.
ROOT = Path(globals().get("SPECPATH", os.getcwd())).resolve()


def _normalize_binaries(binaries: list[tuple[str, str]]):
    """
    某些 GDAL/PROJ 运行时 DLL（例如 gdal.dll / proj_9.dll）如果放在子目录，
    Windows loader 可能找不到。这里把关键 DLL 提升到根目录，减少环境依赖。
    """
    promote = {
        "gdal.dll",
        "proj_9.dll",
        "geos.dll",
        "geos_c.dll",
    }
    out: list[tuple[str, str]] = []
    for src, dest in binaries:
        name = Path(src).name.lower()
        if name in promote:
            out.append((src, "."))
        else:
            out.append((src, dest))
    return out


hiddenimports = []
hiddenimports += collect_submodules("osgeo")
hiddenimports += collect_submodules("osgeo_utils")

# Optional deps used by some GDAL utilities; safe to include if installed.
try:
    hiddenimports += collect_submodules("numpy")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("PIL")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("pyproj")
except Exception:
    pass

datas = []
datas += collect_data_files("osgeo", include_py_files=True)
datas += collect_data_files("osgeo_utils", include_py_files=True)
try:
    datas += collect_data_files("pyproj", include_py_files=False)
except Exception:
    pass

binaries = []
binaries += collect_dynamic_libs("osgeo")
try:
    binaries += collect_dynamic_libs("pyproj")
except Exception:
    pass
binaries = _normalize_binaries(binaries)


a = Analysis(
    ["geotiff_tool.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GeoTiffTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # tkinter GUI
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GeoTiffTool",
)

