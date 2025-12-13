# GeoMosaic

一个桌面端工具：将 GeoTIFF 影像一键切成 WebGIS 可用的 XYZ 瓦片（PNG，支持透明），并自动生成 Leaflet + Google Satellite 底图的 `index.html` 预览页。

## 运行

- 建议使用虚拟环境（避免把 GDAL 装到全局 Python）：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install .\GDAL-3.10.1-cp312-cp312-win_amd64.whl
.\.venv\Scripts\python run_gui.py
```

## 输出结构

- 输出目录根下直接生成标准 XYZ 结构：`{z}/{x}/{y}.png`
- 同时生成：`index.html`（Leaflet 预览页，默认加载 Google Satellite 作为底图并叠加本地瓦片）

> 备注：Google 卫星瓦片 URL 属于非官方方式，可能受限流/策略影响；用于本地预览一般可用。
