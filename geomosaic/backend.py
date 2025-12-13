from __future__ import annotations

import math
import os
import contextlib
import io
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _runtime_base_dir() -> Path:
    # PyInstaller sets sys._MEIPASS. In onedir it points to the app folder.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            return Path(meipass).resolve()
        except Exception:
            pass
    return Path(sys.executable).resolve().parent


def _preconfigure_dll_search_path_for_frozen() -> None:
    """Make sure GDAL/PROJ dlls are discoverable in a frozen build."""
    if not _is_frozen():
        return

    base = _runtime_base_dir()
    dll_dirs = [
        base,
        base / "osgeo",
    ]

    # Prefer robust DLL search path on py>=3.8
    for d in dll_dirs:
        try:
            if d.exists():
                os.add_dll_directory(str(d))  # type: ignore[attr-defined]
        except Exception:
            pass

    # Also prepend PATH for libraries using legacy search.
    existing = os.environ.get("PATH", "")
    prefix = os.pathsep.join(str(d) for d in dll_dirs if d.exists())
    if prefix and (prefix not in existing):
        os.environ["PATH"] = prefix + os.pathsep + existing


# Must run BEFORE importing osgeo in frozen builds
_preconfigure_dll_search_path_for_frozen()

try:
    from osgeo import gdal, osr  # type: ignore
except Exception as e:  # pragma: no cover
    gdal = None  # type: ignore
    osr = None  # type: ignore
    _OSGEO_IMPORT_ERROR = e
else:
    _OSGEO_IMPORT_ERROR = None
    _GDAL_RUNTIME_CONFIGURED = False


LogFn = Callable[[str], None]


@dataclass(frozen=True)
class RasterPreviewInfo:
    warped_path: Path
    center_lat: float
    center_lng: float
    bounds_sw_lat: float
    bounds_sw_lng: float
    bounds_ne_lat: float
    bounds_ne_lng: float
    suggested_max_zoom: Optional[int]


def environment_hint() -> str:
    exe = sys.executable
    in_venv = (getattr(sys, "base_prefix", sys.prefix) != sys.prefix) or bool(
        os.environ.get("VIRTUAL_ENV")
    )
    return f"Python: {exe} | venv: {'是' if in_venv else '否'}"


def ensure_gdal_available() -> None:
    if _OSGEO_IMPORT_ERROR is not None:
        raise RuntimeError(
            "无法导入 GDAL Python 绑定 (osgeo)。\n"
            f"{environment_hint()}\n\n"
            "请确认你已在当前 Python/虚拟环境中安装 GDAL。\n"
            "Windows 常见做法：在项目根目录执行：\n"
            "  python -m pip install .\\GDAL-3.10.1-cp312-cp312-win_amd64.whl\n"
            "然后再运行本程序。\n\n"
            f"原始错误: {_OSGEO_IMPORT_ERROR}"
        )
    _configure_gdal_runtime()


def _configure_gdal_runtime() -> None:
    """Configure GDAL/PROJ runtime data paths.

    典型报错：
    - PROJ: proj_create_from_database: Cannot find proj.db
    - Invalid SRS for -t_srs
    主要原因是 PROJ/GDAL_DATA 没有指向 wheel 内置的数据目录。
    """

    global _GDAL_RUNTIME_CONFIGURED
    if _GDAL_RUNTIME_CONFIGURED:
        return

    # Enable Python exceptions (silences GDAL 4.0 future warning too).
    try:
        gdal.UseExceptions()  # type: ignore[attr-defined]
    except Exception:
        pass

    # Prefer official EPSG definition over GeoTIFF keys to avoid mismatch warnings.
    for k, v in [
        ("GTIFF_SRS_SOURCE", "EPSG"),
        ("OSR_USE_NON_DEPRECATED", "YES"),
    ]:
        os.environ.setdefault(k, v)
        try:
            gdal.SetConfigOption(k, v)  # type: ignore[attr-defined]
        except Exception:
            pass

    # Detect data directories from installed osgeo package.
    try:
        osgeo_root = Path(getattr(gdal, "__file__", "")).resolve().parent  # type: ignore[arg-type]
    except Exception:
        osgeo_root = None

    proj_dir_candidates: list[Path] = []
    gdal_data_candidates: list[Path] = []

    if osgeo_root:
        proj_dir_candidates.append(osgeo_root / "data" / "proj")
        gdal_data_candidates.append(osgeo_root / "data" / "gdal")

    # Fallback: if pyproj is installed, reuse its PROJ data directory.
    try:
        from pyproj.datadir import get_data_dir  # type: ignore

        proj_dir_candidates.append(Path(get_data_dir()).resolve())
    except Exception:
        pass

    proj_dir = next((p for p in proj_dir_candidates if (p / "proj.db").exists()), None)
    gdal_data_dir = next((p for p in gdal_data_candidates if p.exists()), None)

    current_proj_lib = os.environ.get("PROJ_LIB")
    current_proj_ok = False
    if current_proj_lib:
        try:
            current_proj_ok = (Path(current_proj_lib) / "proj.db").exists()
        except Exception:
            current_proj_ok = False

    if proj_dir is not None and not current_proj_ok:
        os.environ["PROJ_LIB"] = str(proj_dir)
        try:
            gdal.SetConfigOption("PROJ_LIB", str(proj_dir))  # type: ignore[attr-defined]
        except Exception:
            pass

    current_gdal_data = os.environ.get("GDAL_DATA")
    current_gdal_ok = False
    if current_gdal_data:
        try:
            current_gdal_ok = Path(current_gdal_data).exists()
        except Exception:
            current_gdal_ok = False

    if gdal_data_dir is not None and not current_gdal_ok:
        os.environ["GDAL_DATA"] = str(gdal_data_dir)
        try:
            gdal.SetConfigOption("GDAL_DATA", str(gdal_data_dir))  # type: ignore[attr-defined]
        except Exception:
            pass

    # Smoke-test EPSG parsing; if it fails, raise a clear error.
    try:
        srs = osr.SpatialReference()  # type: ignore[call-arg]
        rc = srs.ImportFromEPSG(3857)  # type: ignore[attr-defined]
        if rc != 0:
            raise RuntimeError(f"ImportFromEPSG(3857) 返回错误码：{rc}")
    except Exception as e:
        raise RuntimeError(
            "PROJ/GDAL 坐标库初始化失败：无法解析 EPSG:3857。\n"
            f"{environment_hint()}\n"
            f"PROJ_LIB={os.environ.get('PROJ_LIB')}\n"
            f"GDAL_DATA={os.environ.get('GDAL_DATA')}\n\n"
            "建议：确认你使用的是同一个虚拟环境运行，并且 GDAL wheel 内置数据目录完整。\n"
            f"原始错误：{e}"
        ) from e

    _GDAL_RUNTIME_CONFIGURED = True


def validate_geotiff(src_path: Path) -> None:
    ensure_gdal_available()

    ds = gdal.OpenEx(str(src_path), gdal.OF_RASTER)  # type: ignore
    if ds is None:
        raise RuntimeError(f"无法读取影像：{src_path}")

    proj = (ds.GetProjectionRef() or "").strip()
    gt = ds.GetGeoTransform(can_return_null=True)

    if not proj:
        raise RuntimeError("该 TIFF 缺少投影信息 (Projection)。请确认它是 GeoTIFF。")
    if gt is None:
        raise RuntimeError("该 TIFF 缺少地理参考 (GeoTransform)。请确认它是 GeoTIFF。")


def warp_to_web_mercator(
    src_path: Path,
    out_dir: Path,
    log: LogFn,
) -> RasterPreviewInfo:
    ensure_gdal_available()

    out_dir.mkdir(parents=True, exist_ok=True)
    # 避免把中间文件直接丢在输出根目录：统一放到缓存子目录
    cache_dir = out_dir / "_geomosaic_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    warped_path = cache_dir / "warped_3857.tif"

    log("读取与校验 GeoTIFF...")
    validate_geotiff(src_path)

    ds = gdal.OpenEx(str(src_path), gdal.OF_RASTER)  # type: ignore
    assert ds is not None

    log("智能重投影：确保 EPSG:3857 (Web Mercator)...")

    # Nodata handling: prefer existing nodata, otherwise let Warp decide.
    src_nodata = None
    try:
        b1 = ds.GetRasterBand(1)
        if b1 is not None:
            src_nodata = b1.GetNoDataValue()
    except Exception:
        src_nodata = None

    warp_kwargs: dict = {
        "dstSRS": "EPSG:3857",
        "format": "GTiff",
        "resampleAlg": gdal.GRA_Bilinear,  # type: ignore
        "multithread": True,
        # Key: create alpha so outside area becomes transparent
        "dstAlpha": True,
        # Ensure destination is initialized as nodata (transparent)
        "warpOptions": ["INIT_DEST=NO_DATA"],
        "creationOptions": [
            "TILED=YES",
            "COMPRESS=DEFLATE",
            "PREDICTOR=2",
            "BIGTIFF=IF_SAFER",
        ],
    }
    if src_nodata is not None:
        warp_kwargs["srcNodata"] = src_nodata
        warp_kwargs["dstNodata"] = 0

    opts = gdal.WarpOptions(**warp_kwargs)  # type: ignore
    res = gdal.Warp(str(warped_path), ds, options=opts)  # type: ignore
    if res is None:
        raise RuntimeError("GDAL Warp 失败：无法重投影到 EPSG:3857")
    res = None

    info = _compute_preview_info(warped_path)
    if info.suggested_max_zoom is not None:
        log(f"建议最大缩放级别：{info.suggested_max_zoom}")
    return info


def _compute_preview_info(warped_path: Path) -> RasterPreviewInfo:
    ensure_gdal_available()

    ds = gdal.OpenEx(str(warped_path), gdal.OF_RASTER)  # type: ignore
    if ds is None:
        raise RuntimeError("无法读取重投影结果")

    gt = ds.GetGeoTransform(can_return_null=True)
    if gt is None:
        raise RuntimeError("重投影结果缺少 GeoTransform")

    w = ds.RasterXSize
    h = ds.RasterYSize

    def px_to_xy(px: float, py: float) -> tuple[float, float]:
        x = gt[0] + px * gt[1] + py * gt[2]
        y = gt[3] + px * gt[4] + py * gt[5]
        return x, y

    corners_3857 = [
        px_to_xy(0, 0),
        px_to_xy(w, 0),
        px_to_xy(w, h),
        px_to_xy(0, h),
    ]

    s_3857 = osr.SpatialReference()  # type: ignore
    s_3857.ImportFromEPSG(3857)  # type: ignore
    s_4326 = osr.SpatialReference()  # type: ignore
    s_4326.ImportFromEPSG(4326)  # type: ignore

    # 关键：修正 GDAL 3+ 可能出现的坐标轴顺序问题（EPSG:4326 轴顺序可能是 lat,lon）。
    # Leaflet/绝大多数 WebGIS 约定是 (lon,lat) / (x,y) 的传统 GIS 顺序。
    try:
        s_3857.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # type: ignore[attr-defined]
        s_4326.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # type: ignore[attr-defined]
    except Exception:
        pass

    ct = osr.CoordinateTransformation(s_3857, s_4326)  # type: ignore

    lats: list[float] = []
    lngs: list[float] = []
    for x, y in corners_3857:
        a, b, *_ = ct.TransformPoint(x, y)
        lon = float(a)
        lat = float(b)

        # 兜底：如果仍然出现顺序颠倒（例如 lat 超出 ±90），自动交换
        if abs(lat) > 90 and abs(lon) <= 90:
            lon, lat = lat, lon

        lats.append(lat)
        lngs.append(lon)

    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    center_lat = (min_lat + max_lat) / 2.0
    center_lng = (min_lng + max_lng) / 2.0

    suggested = _suggest_max_zoom_from_3857_gt(gt)

    return RasterPreviewInfo(
        warped_path=warped_path,
        center_lat=center_lat,
        center_lng=center_lng,
        bounds_sw_lat=min_lat,
        bounds_sw_lng=min_lng,
        bounds_ne_lat=max_lat,
        bounds_ne_lng=max_lng,
        suggested_max_zoom=suggested,
    )


def _suggest_max_zoom_from_3857_gt(gt) -> Optional[int]:
    # WebMercator resolution at equator: 156543.03392804097 / 2^z (meters/pixel)
    # gt[1] is pixel width in map units for north-up images.
    try:
        res = abs(float(gt[1]))
        if not (res > 0):
            return None
        z = math.log2(156543.03392804097 / res)
        # Clamp to common web range
        return int(max(0, min(22, math.ceil(z))))
    except Exception:
        return None


def generate_xyz_tiles(
    warped_path: Path,
    out_dir: Path,
    min_zoom: int,
    max_zoom: int,
    log: LogFn,
) -> None:
    ensure_gdal_available()

    if min_zoom < 0 or max_zoom < 0 or max_zoom < min_zoom:
        raise ValueError("Zoom 范围不合法：min_zoom/max_zoom")

    out_dir.mkdir(parents=True, exist_ok=True)

    zoom_arg = f"{min_zoom}-{max_zoom}"

    # Make sure we do XYZ scheme and PNG tiles; disable built-in webviewer.
    argv = [
        "gdal2tiles.py",
        "--profile=mercator",
        f"--zoom={zoom_arg}",
        "--xyz",
        "--tiledriver=PNG",
        "--webviewer=none",
        "--resume",
        "--exclude",
        "--resampling=bilinear",
        str(warped_path),
        str(out_dir),
    ]

    log("开始生成 XYZ 瓦片...")
    log("命令：python -m osgeo_utils.gdal2tiles " + " ".join(_quote_for_log(a) for a in argv[1:]))

    # In a packaged exe, sys.executable is not Python, so subprocess "python -m" is not viable.
    # Even in normal mode, in-process invocation is more reliable (same env, same GDAL_DATA/PROJ_LIB).
    _run_gdal2tiles_inprocess(argv=argv, log=log)


class _LogStream(io.TextIOBase):
    def __init__(self, log: LogFn) -> None:
        super().__init__()
        self._log = log
        self._buf = ""

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        self._buf += s.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._log(line.rstrip())
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        if self._buf.strip():
            self._log(self._buf.rstrip())
        self._buf = ""


class _ForwardToLogHandler(logging.Handler):
    def __init__(self, log: LogFn) -> None:
        super().__init__()
        self._log = log

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        if msg.strip():
            self._log(msg)


def _run_gdal2tiles_inprocess(argv: list[str], log: LogFn) -> None:
    try:
        from osgeo_utils import gdal2tiles  # type: ignore
    except Exception as e:
        raise RuntimeError(f"无法导入 osgeo_utils.gdal2tiles：{e}") from e

    # Forward logging from gdal2tiles module.
    g2t_logger = logging.getLogger("gdal2tiles")
    handler = _ForwardToLogHandler(log)
    handler.setFormatter(logging.Formatter("%(message)s"))
    old_level = g2t_logger.level
    g2t_logger.addHandler(handler)
    g2t_logger.setLevel(logging.INFO)

    # Also capture stdout/stderr (progress bars / prints).
    stream = _LogStream(log)
    try:
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            rc = int(gdal2tiles.main(argv, called_from_main=True))
    finally:
        try:
            stream.flush()
        except Exception:
            pass
        g2t_logger.removeHandler(handler)
        g2t_logger.setLevel(old_level)

    if rc != 0:
        raise RuntimeError(f"gdal2tiles 执行失败，退出码={rc}")


def guess_xyz_tiles_url_template(out_dir: Path) -> tuple[str, Path | None]:
    """Guess tiles url template relative to out_dir/index.html.

    正常情况下 gdal2tiles 会在 out_dir 下生成：
      {z}/{x}/{y}.png

    但在某些环境/参数组合下，可能会多一层目录（例如 out_dir/tiles/{z}/{x}/{y}.png）。
    这里做一个轻量探测，让 index.html 不至于空白。
    """

    # 1) Prefer direct numeric zoom directories in output root
    root = _find_tiles_root(out_dir)
    rel = root.relative_to(out_dir).as_posix()
    prefix = "." if rel == "." else f"./{rel}"
    template = f"{prefix}/{{z}}/{{x}}/{{y}}.png"
    sample = _find_any_png_under(root)
    return template, sample


def _find_tiles_root(out_dir: Path) -> Path:
    # Check out_dir/{0..30} existence first (fast path)
    for z in range(0, 31):
        p = out_dir / str(z)
        if p.is_dir():
            return out_dir

    # One-level deep search for a directory containing numeric zoom dirs
    try:
        for child in out_dir.iterdir():
            if not child.is_dir():
                continue
            for z in range(0, 31):
                if (child / str(z)).is_dir():
                    return child
    except Exception:
        pass

    # Fallback: assume output root
    return out_dir


def _find_any_png_under(root: Path) -> Path | None:
    # Depth-limited search for a sample tile path for debugging.
    # root/{z}/{x}/{y}.png
    try:
        for z_dir in root.iterdir():
            if not z_dir.is_dir() or not z_dir.name.isdigit():
                continue
            for x_dir in z_dir.iterdir():
                if not x_dir.is_dir() or not x_dir.name.isdigit():
                    continue
                for f in x_dir.iterdir():
                    if f.is_file() and f.suffix.lower() == ".png":
                        return f
    except Exception:
        return None
    return None


def _detect_gdal2tiles_command(log: LogFn) -> list[str]:
    # Prefer module invocation to ensure we use the same interpreter/environment.
    candidates: list[list[str]] = [
        [sys.executable, "-m", "osgeo_utils.gdal2tiles"],
        [sys.executable, "-m", "gdal2tiles"],
        ["gdal2tiles"],
        ["gdal2tiles.py"],
    ]

    for cmd in candidates:
        if _probe_command(cmd + ["--help"]):
            log(f"找到 gdal2tiles：{' '.join(cmd)}")
            return cmd

    raise RuntimeError(
        "未找到 gdal2tiles。\n"
        f"{environment_hint()}\n\n"
        "请确认 GDAL 安装正确（包含 gdal2tiles）。\n"
        "如果你使用项目根目录的 wheel：\n"
        "  python -m pip install .\\GDAL-3.10.1-cp312-cp312-win_amd64.whl\n"
    )


def _probe_command(cmd: list[str]) -> bool:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
        return p.returncode == 0
    except Exception:
        return False


def _run_with_live_output(args: list[str], cwd: Path, log: LogFn) -> None:
    p = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        shell=False,
    )
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip("\n")
        if line:
            log(line)
    code = p.wait()
    if code != 0:
        raise RuntimeError(f"gdal2tiles 执行失败，退出码={code}")


def _quote_for_log(s: str) -> str:
    if any(c.isspace() for c in s):
        return f'"{s}"'
    return s
