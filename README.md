# GeoMosaic

本仓库包含两个 Windows 桌面端小工具（均为 tkinter GUI）：

- **GeoMosaic**：将 GeoTIFF 影像一键切成 WebGIS 可用的 **XYZ 瓦片（PNG，支持透明）**，并自动生成 **Leaflet 预览页 `index.html`**（默认叠加 Google Satellite 底图，便于本地快速查看效果）。
- **GeoTiffTool**：对 GeoTIFF 的地理参考信息做“提取/写回”：从 GeoTIFF 读取 **GeoTransform / Projection / GCP**，导出为 JSON；再把该 JSON 写回到另一份（已编辑过的）TIFF 中。

## 功能特点

### GeoMosaic（切片）

- **GeoTIFF → Web Mercator(EPSG:3857)**：自动重投影，生成带 Alpha 的中间结果，保证透明背景。
- **生成标准 XYZ 目录结构**：`{z}/{x}/{y}.png`
- **自动生成预览页**：输出目录下生成 `index.html`，打开即可预览（`fitBounds` 自动定位到影像范围）。
- **尽量减少环境坑**：运行时会自动尝试配置 `PROJ_LIB` / `GDAL_DATA`，并输出当前 Python/venv 信息。

### GeoTiffTool（坐标处理）

- **坐标提取**：读取 GeoTIFF 的 GeoTransform / Projection WKT / GCP（如存在），并导出为 JSON（格式版本：`geomosaic_georef_v1`）。
- **坐标嵌入**：将导出的 JSON 写入到另一份 TIFF（先复制再写入，避免破坏原文件），写入内容包括 GeoTransform / Projection / GCP。
- **预览优先**：两步操作都有预览区，确认无误后再保存/写入。

## 环境要求

- **Windows + Python 3.12（推荐）**：仓库内的 GDAL wheel 为 `cp312`，需与 Python 版本匹配。
- GUI 使用 **tkinter（Python 自带）**。

> 说明：GDAL 在 Windows 上强烈建议使用与你 Python 版本一致的 wheel；否则容易出现 `osgeo` 导入失败或 `proj.db` 找不到等问题。

## 安装（Windows / 推荐虚拟环境）

> 重点：下面所有安装命令都使用 `\.venv\Scripts\python -m pip ...`，确保安装到虚拟环境，而不是全局 Python。

```powershell
# 1) 创建并进入虚拟环境
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip setuptools wheel

# 2) 安装 GDAL（请使用与你 Python 版本匹配的 wheel）
# 如果你仓库根目录有 GDAL-3.10.1-cp312-cp312-win_amd64.whl，可直接安装：
.\.venv\Scripts\python -m pip install .\GDAL-3.10.1-cp312-cp312-win_amd64.whl

# 3) 安装其它依赖（建议装上，尤其 pyproj 用于定位 PROJ 数据目录）
.\.venv\Scripts\python -m pip install -U numpy pillow pyproj
```

## 运行

### 启动 GUI（推荐）

二选一：

```powershell
# 方式 A：运行仓库根目录脚本
.\.venv\Scripts\python run_gui.py

# 方式 B：模块方式启动（等价）
.\.venv\Scripts\python -m geomosaic
```

启动后：选择输入 GeoTIFF、选择输出目录、设置 `Min Zoom/Max Zoom`，点击“开始切片”。程序会在日志里提示当前 Python 路径以及是否处于 venv。

### 启动 GeoTiffTool（坐标处理 GUI）

```powershell
.\.venv\Scripts\python .\geotiff_tool.py
```

启动后分两页：

- **1. 坐标提取（GeoTIFF → JSON）**：选择一个 GeoTIFF，工具会自动读取并预览；再选择保存路径并点击“保存地理信息”。
- **2. 坐标嵌入（JSON → GeoTIFF）**：选择“坐标文件（JSON）”与“编辑后的 TIFF”，点击“应用并保存 GeoTIFF”输出新文件（默认在文件名后加 `_georef`）。

> 说明：界面里虽然允许把输出扩展名选成 `.txt/.geo`，但内容仍然是 **JSON**；推荐直接使用 `.json` 扩展名以免误解。

## 输出结构

### GeoMosaic 输出

输出目录（你选择的目标文件夹）下将生成：

- **XYZ 瓦片**：`{z}/{x}/{y}.png`
- **预览页**：`index.html`
- **缓存目录**：`_geomosaic_cache/`（默认流程结束后会尽量清理；勾选“保留中间文件”会保留其中的 `warped_3857.tif`）

> 备注：预览页中的 Google 卫星瓦片 URL 属于非官方方式，可能受限流/策略影响；用于本地预览通常可用。

### GeoTiffTool 输出（JSON 格式）

提取导出的 JSON（`geomosaic_georef_v1`）包含（部分字段）：

- `raster_size`: `[width, height]`
- `geotransform`: 6 参数数组（若不存在则为 `null`）
- `projection_wkt`: 投影 WKT（可能为空字符串）
- `gcps`: GCP 列表（可能为空）
- `gcp_projection_wkt`: GCP 的投影 WKT（可能为空字符串）

## 常见问题（GDAL/PROJ）

- **无法导入 `osgeo` / GDAL**
  - 现象：启动后提示 “无法导入 GDAL Python 绑定 (osgeo)”
  - 处理：确认你是在 **同一个虚拟环境** 里安装并运行：

```powershell
.\.venv\Scripts\python -c "import sys; print(sys.executable)"
.\.venv\Scripts\python -c "from osgeo import gdal; print(gdal.__version__)"
```

- **`PROJ: ... Cannot find proj.db` 或 EPSG 解析失败**
  - 处理：优先安装 `pyproj`（它能提供/定位 PROJ 数据目录）；同时确认 GDAL wheel 的数据目录完整。

- **生成瓦片时提示找不到 `gdal2tiles`**
  - 本项目优先通过 `osgeo_utils.gdal2tiles` 在进程内调用；若你的 GDAL 安装不包含它，请更换为包含完整工具链的 GDAL wheel/发行版。

## 打包为 Windows 可执行文件（PyInstaller）

仓库提供了 `build_exe.ps1`，会分别调用 `GeoMosaic.spec` 与 `GeoTiffTool.spec`，以 **onedir** 形式打包，并把 `osgeo` 的 DLL 及 `gdal/proj` 数据一并收集。

```powershell
# 默认使用 .venv\Scripts\python.exe
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1

# 或指定虚拟环境 Python
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Python .\.venv\Scripts\python.exe
```

产物位置：

- `dist\GeoMosaic\GeoMosaic.exe`
- `dist\GeoTiffTool\GeoTiffTool.exe`
