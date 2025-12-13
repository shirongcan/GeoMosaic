from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreviewConfig:
    title: str
    min_zoom: int
    max_zoom: int
    center_lat: float
    center_lng: float
    bounds_sw_lat: float
    bounds_sw_lng: float
    bounds_ne_lat: float
    bounds_ne_lng: float
    tiles_url_template: str = "./{z}/{x}/{y}.png"


def build_leaflet_preview_html(cfg: PreviewConfig) -> str:
    # NOTE: Google Satellite tile URL is unofficial and may be rate-limited.
    # In practice it works for many internal/preview uses.
    # 尽量贴近用户给的“可用模板”：简单、直观、少魔法。
    # 同时保留我们计算出的 bounds，优先 fitBounds 让打开即看到影像范围。
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_escape_html(cfg.title)}</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ width: 100%; height: 100vh; }}
    </style>
</head>
<body>
    <div id="map"></div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // --- 1. 定义图层 ---

        // 底图：谷歌卫星 (Base Layer)
        var googleSat = L.tileLayer('https://{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{
            maxZoom: 20,
            subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
            attribution: 'Google Satellite'
        }});

        // 叠加层：本地瓦片 (Overlay Layer)
        var localTiles = L.tileLayer('{cfg.tiles_url_template}', {{
            minZoom: {cfg.min_zoom},
            maxZoom: {cfg.max_zoom},
            tms: false,
            opacity: 1.0,
            attribution: 'Local Tiles'
        }});

        // --- 2. 初始化地图 ---
        // 先用中心点/建议 zoom 初始化，随后若 bounds 合法则 fitBounds
        var map = L.map('map', {{
            center: [{cfg.center_lat}, {cfg.center_lng}],
            zoom: {min(cfg.max_zoom, max(cfg.min_zoom, max(0, cfg.min_zoom + 2)))},
            layers: [googleSat, localTiles]
        }});

        // --- 3. 图层控制器配置 ---
        var baseMaps = {{
            "谷歌卫星": googleSat
        }};

        var overlayMaps = {{
            "本地瓦片数据": localTiles
        }};

        L.control.layers(baseMaps, overlayMaps).addTo(map);

        // --- 4. 自动定位到影像范围 ---
        var bounds = L.latLngBounds(
            L.latLng({cfg.bounds_sw_lat}, {cfg.bounds_sw_lng}),
            L.latLng({cfg.bounds_ne_lat}, {cfg.bounds_ne_lng})
        );
        if (bounds.isValid()) {{
            map.fitBounds(bounds, {{ padding: [20, 20] }});
        }}
    </script>
</body>
</html>
"""


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
