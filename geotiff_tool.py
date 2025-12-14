import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont
import os
import json
import shutil
import sys
import subprocess
from pathlib import Path
import warnings
import importlib.util


def _set_env_if_missing(var_name: str, value: str) -> bool:
    if os.environ.get(var_name):
        return False
    os.environ[var_name] = value
    return True


def _try_configure_proj_and_gdal_data() -> None:
    """
    在 Windows/venv 场景下，GDAL/PROJ 的数据文件（proj.db、gcs.csv 等）
    可能无法自动定位，导致 Warning 1: Cannot find proj.db。

    这里做一个“尽力而为”的自动探测：如果找得到就设置 PROJ_LIB / GDAL_DATA。
    """
    prefix = Path(sys.prefix)

    # 兜底：直接从 osgeo 包位置推断 data 目录（不依赖 sys.prefix 是否指向 venv）
    osgeo_data_dir: Path | None = None
    try:
        spec = importlib.util.find_spec("osgeo")
        if spec and spec.submodule_search_locations:
            osgeo_pkg_dir = Path(list(spec.submodule_search_locations)[0])
            cand = osgeo_pkg_dir / "data"
            if cand.is_dir():
                osgeo_data_dir = cand
    except Exception:
        osgeo_data_dir = None

    proj_candidates = [
        # 常见 venv / 系统布局
        prefix / "share" / "proj",
        prefix / "Library" / "share" / "proj",  # conda
        # pyproj 自带的 proj 数据目录（如果装了 pyproj）
        prefix / "Lib" / "site-packages" / "pyproj" / "proj_dir" / "share" / "proj",
        # 某些打包方式可能放在 osgeo 包内
        prefix / "Lib" / "site-packages" / "osgeo" / "data" / "proj",
        prefix / "Lib" / "site-packages" / "osgeo" / "data",
    ]

    if osgeo_data_dir is not None:
        proj_candidates.insert(0, osgeo_data_dir / "proj")
        proj_candidates.insert(1, osgeo_data_dir)

    for p in proj_candidates:
        try:
            if (p / "proj.db").is_file():
                # PROJ_LIB 是旧变量，PROJ_DATA 是新变量；两者都设置最兼容
                _set_env_if_missing("PROJ_LIB", str(p))
                _set_env_if_missing("PROJ_DATA", str(p))
                break
        except OSError:
            continue

    gdal_data_candidates = [
        prefix / "share" / "gdal",
        prefix / "Library" / "share" / "gdal",  # conda
        prefix / "Lib" / "site-packages" / "osgeo" / "data" / "gdal",
        prefix / "Lib" / "site-packages" / "osgeo" / "data",
    ]

    if osgeo_data_dir is not None:
        gdal_data_candidates.insert(0, osgeo_data_dir / "gdal")
        gdal_data_candidates.insert(1, osgeo_data_dir)

    for p in gdal_data_candidates:
        try:
            if p.is_dir():
                # gdal 数据目录里通常会有 gcs.csv / pcs.csv 等文件
                if (p / "gcs.csv").is_file() or (p / "pcs.csv").is_file():
                    _set_env_if_missing("GDAL_DATA", str(p))
                    break
        except OSError:
            continue


_try_configure_proj_and_gdal_data()

try:
    from osgeo import gdal
except Exception as _e:  # pragma: no cover
    gdal = None
    _GDAL_IMPORT_ERROR = str(_e)
else:
    _GDAL_IMPORT_ERROR = ""
    # 避免 FutureWarning，并统一以异常方式抛出 GDAL 错误
    try:
        gdal.UseExceptions()
    except Exception:
        pass
    # 降噪：把“未显式 UseExceptions”的 FutureWarning 静音（在极端情况下仍可能出现）
    warnings.filterwarnings(
        "ignore",
        message=r".*UseExceptions\(\).*",
        category=FutureWarning,
        module=r"osgeo\.gdal",
    )

class GeoTiffToolApp:
    def __init__(self, master):
        self.master = master
        master.title("GeoTIFF 坐标处理工具")
        master.geometry("980x680")
        master.minsize(860, 600)

        self._init_style()

        # Tab1 提取后的数据缓存（用于预览与保存）
        self._extract_data: dict | None = None
        # Tab2 嵌入页：坐标文件解析缓存 & 目标 tiff 信息缓存
        self._embed_georef_data: dict | None = None
        self._embed_target_data: dict | None = None

        self.status_var = tk.StringVar(value="就绪")
        
        # --- 顶部标题区 ---
        header = ttk.Frame(master, padding=(14, 12, 14, 8), style="Header.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="GeoTIFF 坐标处理工具", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="选择文件后自动读取并预览；确认无误后再保存/写入。",
            style="SubTitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # --- 创建 Notebook (Tab 容器) ---
        self.notebook = ttk.Notebook(master)
        self.notebook.pack(pady=(0, 10), padx=12, expand=True, fill="both")

        # --- Tab 1: 坐标提取 (GeoTIFF -> TXT/JSON) ---
        self.tab_extract = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_extract, text=" 1. 坐标提取 (GeoTIFF → JSON/TXT) ")
        self.create_extract_tab(self.tab_extract)

        # --- Tab 2: 坐标嵌入 (TXT/JSON -> GeoTIFF) ---
        self.tab_embed = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_embed, text=" 2. 坐标嵌入 (JSON/TXT → GeoTIFF) ")
        self.create_embed_tab(self.tab_embed)

        # --- 底部状态栏 ---
        status = ttk.Label(master, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status.pack(side="bottom", fill="x")

    def _init_style(self) -> None:
        style = ttk.Style(self.master)
        # 为了实现可控的浅色卡片风/主按钮上色，优先使用 clam（更支持自定义颜色）
        for t in ("clam", "vista", "xpnative"):
            if t in style.theme_names():
                try:
                    style.theme_use(t)
                    break
                except Exception:
                    continue

        # 字体（Windows 优先 Segoe UI / Consolas，失败则回退默认字体）
        try:
            self.ui_font = tkfont.Font(family="Segoe UI", size=10)
            self.ui_font_bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
            self.title_font = tkfont.Font(family="Segoe UI", size=14, weight="bold")
            self.mono_font = tkfont.Font(family="Consolas", size=10)
        except Exception:
            self.ui_font = tkfont.nametofont("TkDefaultFont")
            self.ui_font_bold = tkfont.nametofont("TkDefaultFont").copy()
            self.ui_font_bold.configure(weight="bold")
            self.title_font = tkfont.nametofont("TkDefaultFont").copy()
            self.title_font.configure(size=self.ui_font.cget("size") + 4, weight="bold")
            self.mono_font = tkfont.nametofont("TkFixedFont")

        # 颜色系统：浅色背景 + 白色卡片
        self._ui_bg = "#F5F7FB"
        self._card_bg = "#FFFFFF"
        self._border = "#E5E7EB"
        self._text = "#111827"
        self._muted = "#6B7280"
        self._primary = "#2563EB"
        self._primary_hover = "#1D4ED8"
        self._primary_pressed = "#1E40AF"

        try:
            self.master.configure(background=self._ui_bg)
        except Exception:
            pass

        # 全局控件基础样式
        style.configure(".", font=self.ui_font)
        style.configure("TFrame", background=self._ui_bg)
        style.configure("Header.TFrame", background=self._ui_bg)
        style.configure("TLabel", background=self._ui_bg, foreground=self._text, font=self.ui_font)
        style.configure("TButton", font=self.ui_font, padding=(10, 6))
        style.configure("TEntry", font=self.ui_font, padding=(8, 6))
        # clam 下可用；其他主题会忽略，不影响功能
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=self._text)
        # Tab：明确区分选中/未选中/悬停，避免“看起来反了”
        style.configure(
            "TNotebook.Tab",
            font=self.ui_font,
            padding=(12, 7),
            background="#E9EEF6",   # 未选中：浅灰蓝
            foreground=self._muted, # 未选中：灰字
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", self._card_bg),     # 选中：白底（与内容卡片一致）
                ("active", "#DDE7FF"),           # 悬停：略深
                ("!selected", "#E9EEF6"),
            ],
            foreground=[
                ("selected", self._text),        # 选中：深色字
                ("active", self._text),
                ("!selected", self._muted),
            ],
        )

        style.configure("Title.TLabel", font=self.title_font)
        style.configure("SubTitle.TLabel", background=self._ui_bg, foreground=self._muted, font=self.ui_font)
        style.configure("Status.TLabel", padding=(10, 6))

        # Notebook 背景更“干净”
        style.configure("TNotebook", background=self._ui_bg, borderwidth=0, tabmargins=(2, 2, 2, 0))

        # 卡片（LabelFrame）风格：白底 + 细边框
        style.configure(
            "Card.TLabelframe",
            background=self._card_bg,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=self._card_bg,
            foreground=self._text,
            font=self.ui_font_bold,
        )
        style.configure("Card.TFrame", background=self._card_bg)
        style.configure("Card.TLabel", background=self._card_bg, foreground=self._text, font=self.ui_font)

        # 主按钮：蓝色填充 + 悬停/按下态
        style.configure(
            "Primary.TButton",
            font=self.ui_font_bold,
            padding=(12, 7),
            foreground="#FFFFFF",
            background=self._primary,
            borderwidth=0,
            focusthickness=0,
            focuscolor="none",
        )
        style.map(
            "Primary.TButton",
            background=[("pressed", self._primary_pressed), ("active", self._primary_hover)],
            foreground=[("disabled", "#E5E7EB"), ("!disabled", "#FFFFFF")],
        )

        # 次按钮：卡片边框风格（用于“打开输出目录”等）
        style.configure(
            "Secondary.TButton",
            padding=(10, 6),
            foreground=self._text,
            background=self._card_bg,
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#F3F4F6"), ("pressed", "#E5E7EB")],
        )

    def set_status(self, text: str) -> None:
        if hasattr(self, "status_var"):
            self.status_var.set(text)

    def _open_directory(self, directory: str) -> None:
        directory = (directory or "").strip()
        if not directory:
            messagebox.showwarning("提示", "没有可打开的输出目录：请先选择/保存输出文件。")
            return

        directory = os.path.abspath(directory)
        if not os.path.isdir(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"无法创建目录：{directory}\n\n{e}")
                return

        try:
            if sys.platform.startswith("win"):
                os.startfile(directory)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", directory])
            else:
                subprocess.Popen(["xdg-open", directory])
            self.set_status(f"已打开目录：{directory}")
        except Exception as e:
            messagebox.showerror("错误", f"打开目录失败：{directory}\n\n{e}")

    def _output_dir_from_path(self, path: str, fallback_file: str = "") -> str:
        p = (path or "").strip()
        if p:
            if os.path.isdir(p):
                return p
            d = os.path.dirname(p)
            if d:
                return d
        fb = (fallback_file or "").strip()
        return os.path.dirname(fb) if fb else ""

    # --- Tab 1 布局和逻辑：坐标提取 ---
    def create_extract_tab(self, tab):
        # 容器框架
        frame = ttk.Frame(tab, padding=(14, 12, 14, 14))
        frame.pack(expand=True, fill="both")

        # 输入 GeoTIFF 路径
        ttk.Label(frame, text="原始 GeoTIFF 文件：").grid(row=0, column=0, sticky="w", pady=5)
        self.extract_input_path = ttk.Entry(frame, width=50)
        self.extract_input_path.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(frame, text="选择文件", command=self.select_extract_input).grid(row=0, column=2, padx=5, pady=5)

        # 输出坐标文件路径
        ttk.Label(frame, text="输出坐标文件 (TXT/GEO)：").grid(row=1, column=0, sticky="w", pady=5)
        self.extract_output_path = ttk.Entry(frame, width=50)
        self.extract_output_path.grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(frame, text="保存为...", command=self.select_extract_output).grid(row=1, column=2, padx=5, pady=5)

        # 操作按钮
        btn_row = ttk.Frame(frame)
        btn_row.grid(row=2, column=0, columnspan=3, sticky="e", pady=(10, 5))
        ttk.Button(btn_row, text="重新提取/刷新预览", command=self.refresh_extract_preview).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="打开输出目录", style="Secondary.TButton", command=self.open_extract_output_dir).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="保存地理信息", style="Primary.TButton", command=self.save_extracted_georef).pack(side="left")

        # 预览区域
        preview_group = ttk.LabelFrame(frame, text="地理信息预览", padding="8 8 8 8", style="Card.TLabelframe")
        preview_group.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(10, 0))

        self.extract_preview_text = tk.Text(
            preview_group,
            height=10,
            wrap="none",
            font=getattr(self, "mono_font", None),
            padx=8,
            pady=6,
        )
        # Text 不是 ttk：手动做成浅色卡片内的观感
        try:
            self.extract_preview_text.configure(
                background=self._card_bg,
                foreground=self._text,
                insertbackground=self._text,
                relief="solid",
                borderwidth=1,
                highlightthickness=1,
                highlightbackground=self._border,
                highlightcolor=self._border,
            )
        except Exception:
            pass
        y_scroll = ttk.Scrollbar(preview_group, orient="vertical", command=self.extract_preview_text.yview)
        x_scroll = ttk.Scrollbar(preview_group, orient="horizontal", command=self.extract_preview_text.xview)
        self.extract_preview_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.extract_preview_text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        preview_group.grid_rowconfigure(0, weight=1)
        preview_group.grid_columnconfigure(0, weight=1)
        
        # 确保列可以扩展
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(3, weight=1)

    def open_extract_output_dir(self) -> None:
        out_dir = self._output_dir_from_path(
            self.extract_output_path.get(),
            fallback_file=self.extract_input_path.get(),
        )
        self._open_directory(out_dir)

    def select_extract_input(self):
        file_path = filedialog.askopenfilename(
            defaultextension=".tif", 
            filetypes=[("GeoTIFF files", "*.tif;*.tiff")]
        )
        if file_path:
            self.extract_input_path.delete(0, tk.END)
            self.extract_input_path.insert(0, file_path)
            # 选择 tif 后立即提取并预览
            self._extract_and_show(file_path)

    def select_extract_output(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("GeoRef JSON", "*.json"), ("Text files", "*.txt"), ("Geo files", "*.geo")]
        )
        if file_path:
            self.extract_output_path.delete(0, tk.END)
            self.extract_output_path.insert(0, file_path)

    def refresh_extract_preview(self):
        input_file = self.extract_input_path.get()

        if not input_file:
            messagebox.showerror("错误", "请先选择一个 GeoTIFF 文件。")
            return

        self._extract_and_show(input_file)

    def save_extracted_georef(self):
        if self._extract_data is None:
            messagebox.showerror("错误", "尚未提取地理信息。请先选择一个 GeoTIFF 文件。")
            return

        output_file = self.extract_output_path.get().strip()
        if not output_file:
            output_file = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("GeoRef JSON", "*.json"), ("Text files", "*.txt"), ("Geo files", "*.geo")],
            )
            if not output_file:
                return
            self.extract_output_path.delete(0, tk.END)
            self.extract_output_path.insert(0, output_file)

        try:
            self._write_georef_json(self._extract_data, output_file)
            messagebox.showinfo("成功", "地理信息已保存！\n请查看输出文件。")
            self.set_status(f"已保存地理信息：{output_file}")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败！\n\n{e}")
            self.set_status("保存失败")

    def _extract_and_show(self, input_file: str) -> None:
        # 清空旧数据/预览
        self._extract_data = None
        self._set_extract_preview_text("正在读取地理信息，请稍候...")
        self.set_status("正在读取 GeoTIFF 地理信息...")

        if not self._ensure_gdal_available():
            self._set_extract_preview_text("无法导入 osgeo.gdal；请使用安装了 GDAL 的虚拟环境运行。")
            return

        try:
            data = self._extract_georef_data(input_file)
            self._extract_data = data
            self._set_extract_preview_text(self._format_georef_preview(data))
            self.set_status(f"已读取：{os.path.basename(input_file)}")
        except Exception as e:
            self._set_extract_preview_text(f"提取失败：{e}")
            messagebox.showerror("错误", f"坐标信息提取失败！\n\n{e}")
            self.set_status("读取失败")

    def _set_extract_preview_text(self, text: str) -> None:
        if not hasattr(self, "extract_preview_text"):
            return
        self.extract_preview_text.configure(state="normal")
        self.extract_preview_text.delete("1.0", tk.END)
        self.extract_preview_text.insert("1.0", text)
        self.extract_preview_text.configure(state="disabled")

    def _format_georef_preview(self, data: dict) -> str:
        proj = data.get("projection_wkt") or ""
        gcp_proj = data.get("gcp_projection_wkt") or ""
        gt = data.get("geotransform")
        raster_size = data.get("raster_size")
        gcps = data.get("gcps") or []

        def _short_wkt(wkt: str, max_len: int = 800) -> str:
            wkt = (wkt or "").strip()
            if not wkt:
                return "(空)"
            if len(wkt) <= max_len:
                return wkt
            return wkt[:max_len] + "\n...（已截断）"

        lines = []
        lines.append(f"来源文件：{data.get('source_file', '')}")
        if raster_size:
            lines.append(f"栅格大小：{raster_size[0]} x {raster_size[1]}")
        lines.append("")
        lines.append("GeoTransform：")
        lines.append(json.dumps(gt, ensure_ascii=False) if gt is not None else "(空)")
        lines.append("")
        lines.append("Projection WKT：")
        lines.append(_short_wkt(proj))
        lines.append("")
        lines.append(f"GCP 数量：{len(gcps)}")
        if gcps:
            lines.append("前 5 个 GCP：")
            for i, g in enumerate(gcps[:5], start=1):
                lines.append(
                    f"{i}. pixel/line=({g.get('pixel')}, {g.get('line')}) -> x/y/z=({g.get('x')}, {g.get('y')}, {g.get('z')}) id={g.get('id')}"
                )
            lines.append("")
            lines.append("GCP Projection WKT：")
            lines.append(_short_wkt(gcp_proj))

        return "\n".join(lines)

    # --- Tab 2 布局和逻辑：坐标嵌入 ---
    def create_embed_tab(self, tab):
        # 容器框架
        frame = ttk.Frame(tab, padding=(14, 12, 14, 14))
        frame.pack(expand=True, fill="both")

        # 输入坐标文件路径
        ttk.Label(frame, text="坐标文件 (TXT/GEO)：").grid(row=0, column=0, sticky="w", pady=5)
        self.embed_georef_path = ttk.Entry(frame, width=50)
        self.embed_georef_path.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(frame, text="选择文件", command=self.select_embed_georef).grid(row=0, column=2, padx=5, pady=5)

        # 输入编辑后的 TIFF 文件路径
        ttk.Label(frame, text="编辑后的 TIFF 文件：").grid(row=1, column=0, sticky="w", pady=5)
        self.embed_edited_path = ttk.Entry(frame, width=50)
        self.embed_edited_path.grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(frame, text="选择文件", command=self.select_embed_edited).grid(row=1, column=2, padx=5, pady=5)

        # 输出最终 GeoTIFF 文件路径
        ttk.Label(frame, text="输出最终 GeoTIFF 文件：").grid(row=2, column=0, sticky="w", pady=5)
        self.embed_output_path = ttk.Entry(frame, width=50)
        self.embed_output_path.grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(frame, text="保存为...", command=self.select_embed_output).grid(row=2, column=2, padx=5, pady=5)

        # 操作按钮
        btn_row = ttk.Frame(frame)
        btn_row.grid(row=3, column=0, columnspan=3, sticky="e", pady=(10, 5))
        ttk.Button(btn_row, text="刷新预览", command=self.refresh_embed_preview).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="打开输出目录", style="Secondary.TButton", command=self.open_embed_output_dir).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="应用并保存 GeoTIFF", style="Primary.TButton", command=self.apply_embed_and_save).pack(side="left")

        # 预览区域：左=坐标文件，右=目标 TIFF 当前信息（可拖拽分栏）
        preview_group = ttk.LabelFrame(frame, text="嵌入预览", padding="8 8 8 8", style="Card.TLabelframe")
        preview_group.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(10, 0))

        paned = ttk.PanedWindow(preview_group, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 6, 0), style="Card.TFrame")
        right = ttk.Frame(paned, padding=(6, 0, 0, 0), style="Card.TFrame")
        paned.add(left, weight=1)
        paned.add(right, weight=1)

        ttk.Label(left, text="坐标文件内容（将写入）", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(right, text="目标 TIFF 当前地理信息", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.embed_georef_preview = tk.Text(
            left,
            height=10,
            wrap="none",
            font=getattr(self, "mono_font", None),
            padx=8,
            pady=6,
        )
        self.embed_target_preview = tk.Text(
            right,
            height=10,
            wrap="none",
            font=getattr(self, "mono_font", None),
            padx=8,
            pady=6,
        )
        for w in (self.embed_georef_preview, self.embed_target_preview):
            try:
                w.configure(
                    background=self._card_bg,
                    foreground=self._text,
                    insertbackground=self._text,
                    relief="solid",
                    borderwidth=1,
                    highlightthickness=1,
                    highlightbackground=self._border,
                    highlightcolor=self._border,
                )
            except Exception:
                pass

        left_y = ttk.Scrollbar(left, orient="vertical", command=self.embed_georef_preview.yview)
        left_x = ttk.Scrollbar(left, orient="horizontal", command=self.embed_georef_preview.xview)
        self.embed_georef_preview.configure(yscrollcommand=left_y.set, xscrollcommand=left_x.set)

        right_y = ttk.Scrollbar(right, orient="vertical", command=self.embed_target_preview.yview)
        right_x = ttk.Scrollbar(right, orient="horizontal", command=self.embed_target_preview.xview)
        self.embed_target_preview.configure(yscrollcommand=right_y.set, xscrollcommand=right_x.set)

        self.embed_georef_preview.grid(row=1, column=0, sticky="nsew")
        left_y.grid(row=1, column=1, sticky="ns")
        left_x.grid(row=2, column=0, sticky="ew")

        self.embed_target_preview.grid(row=1, column=0, sticky="nsew")
        right_y.grid(row=1, column=1, sticky="ns")
        right_x.grid(row=2, column=0, sticky="ew")

        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # 确保列可以扩展
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(4, weight=1)

    def open_embed_output_dir(self) -> None:
        out_dir = self._output_dir_from_path(
            self.embed_output_path.get(),
            fallback_file=self.embed_edited_path.get(),
        )
        self._open_directory(out_dir)

    def select_embed_georef(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("GeoRef JSON", "*.json"), ("Text files", "*.txt;*.geo")]
        )
        if file_path:
            self.embed_georef_path.delete(0, tk.END)
            self.embed_georef_path.insert(0, file_path)
            self._load_georef_file_and_preview(file_path)
            self.set_status(f"已加载坐标文件：{os.path.basename(file_path)}")

    def select_embed_edited(self):
        file_path = filedialog.askopenfilename(
            defaultextension=".tif", 
            filetypes=[("TIFF files", "*.tif;*.tiff")]
        )
        if file_path:
            self.embed_edited_path.delete(0, tk.END)
            self.embed_edited_path.insert(0, file_path)
            self._load_target_tiff_and_preview(file_path)
            self.set_status(f"已加载目标 TIFF：{os.path.basename(file_path)}")
            # 自动给一个默认输出名（若为空）
            if not self.embed_output_path.get().strip():
                base, ext = os.path.splitext(file_path)
                default_out = base + "_georef" + (ext or ".tif")
                self.embed_output_path.delete(0, tk.END)
                self.embed_output_path.insert(0, default_out)

    def select_embed_output(self):
        # 建议另存为新文件，避免覆盖和风险
        file_path = filedialog.asksaveasfilename(
            defaultextension=".tif", 
            filetypes=[("GeoTIFF files", "*.tif;*.tiff")]
        )
        if file_path:
            self.embed_output_path.delete(0, tk.END)
            self.embed_output_path.insert(0, file_path)

    def refresh_embed_preview(self):
        georef_file = self.embed_georef_path.get().strip()
        edited_file = self.embed_edited_path.get().strip()

        if georef_file:
            self._load_georef_file_and_preview(georef_file)
        if edited_file:
            self._load_target_tiff_and_preview(edited_file)

    def apply_embed_and_save(self):
        georef_file = self.embed_georef_path.get()
        edited_file = self.embed_edited_path.get()
        output_file = self.embed_output_path.get()

        if not georef_file or not edited_file:
            messagebox.showerror("错误", "请选择坐标文件和编辑后的 TIFF 文件。")
            return

        try:
            if not self._ensure_gdal_available():
                return

            # 确保坐标文件已解析
            if self._embed_georef_data is None:
                self._load_georef_file_and_preview(georef_file)
            if self._embed_georef_data is None:
                return

            if not output_file or not output_file.strip():
                output_file = filedialog.asksaveasfilename(
                    defaultextension=".tif",
                    filetypes=[("GeoTIFF files", "*.tif;*.tiff")],
                )
                if not output_file:
                    return
                self.embed_output_path.delete(0, tk.END)
                self.embed_output_path.insert(0, output_file)

            self._apply_georef_from_json(georef_file, edited_file, output_file)
            messagebox.showinfo("成功", "坐标信息嵌入任务成功完成！\n请查看输出文件。")
            self.set_status(f"已输出 GeoTIFF：{output_file}")
        except Exception as e:
            messagebox.showerror("错误", f"坐标信息嵌入任务失败！\n\n{e}")
            self.set_status("写入失败")

    def _set_text_widget(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _load_georef_file_and_preview(self, georef_file: str) -> None:
        self._embed_georef_data = None
        if not hasattr(self, "embed_georef_preview"):
            return

        if not os.path.isfile(georef_file):
            self._set_text_widget(self.embed_georef_preview, f"坐标文件不存在：{georef_file}")
            return

        try:
            with open(georef_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("format") != "geomosaic_georef_v1":
                raise ValueError("坐标文件格式不受支持：请使用本工具导出的 JSON。")
            self._embed_georef_data = data
            self._set_text_widget(self.embed_georef_preview, self._format_georef_preview(data))
        except Exception as e:
            self._set_text_widget(self.embed_georef_preview, f"读取坐标文件失败：{e}")

    def _load_target_tiff_and_preview(self, edited_file: str) -> None:
        self._embed_target_data = None
        if not hasattr(self, "embed_target_preview"):
            return

        if not self._ensure_gdal_available():
            self._set_text_widget(self.embed_target_preview, "无法导入 osgeo.gdal；请使用安装了 GDAL 的虚拟环境运行。")
            return

        if not os.path.isfile(edited_file):
            self._set_text_widget(self.embed_target_preview, f"目标 TIFF 不存在：{edited_file}")
            return

        try:
            data = self._extract_georef_data(edited_file)
            self._embed_target_data = data
            self._set_text_widget(self.embed_target_preview, self._format_georef_preview(data))
        except Exception as e:
            self._set_text_widget(self.embed_target_preview, f"读取目标 TIFF 失败：{e}")

    def _ensure_gdal_available(self) -> bool:
        if gdal is not None:
            return True
        messagebox.showerror(
            "错误",
            "当前 Python 环境无法导入 GDAL（osgeo.gdal）。\n\n"
            "你提到 GDAL 安装在项目虚拟环境里：请确保你是用该虚拟环境的 Python 运行本程序。\n\n"
            f"导入错误信息：{_GDAL_IMPORT_ERROR}",
        )
        return False

    def _extract_georef_data(self, input_file: str) -> dict:
        if not os.path.isfile(input_file):
            raise FileNotFoundError(f"输入文件不存在：{input_file}")

        ds = gdal.Open(input_file, gdal.GA_ReadOnly)
        if ds is None:
            raise RuntimeError("无法打开输入 GeoTIFF（GDAL.Open 返回 None）。")

        try:
            gt = None
            try:
                gt = ds.GetGeoTransform(can_return_null=True)
            except TypeError:
                # 兼容旧版 GDAL：没有 can_return_null 参数
                gt = ds.GetGeoTransform()

            projection_wkt = ds.GetProjection() or ""
            gcp_projection_wkt = ds.GetGCPProjection() or ""
            gcps = ds.GetGCPs() or []

            data = {
                "format": "geomosaic_georef_v1",
                "source_file": os.path.basename(input_file),
                "raster_size": [ds.RasterXSize, ds.RasterYSize],
                "geotransform": list(gt) if gt else None,
                "projection_wkt": projection_wkt,
                "gcp_projection_wkt": gcp_projection_wkt,
                "gcps": [
                    {
                        "id": gcp.Id,
                        "info": gcp.Info,
                        "pixel": gcp.GCPPixel,
                        "line": gcp.GCPLine,
                        "x": gcp.GCPX,
                        "y": gcp.GCPY,
                        "z": gcp.GCPZ,
                    }
                    for gcp in gcps
                ],
                "metadata": ds.GetMetadata() or {},
            }
        finally:
            ds = None

        return data

    def _write_georef_json(self, data: dict, output_file: str) -> None:
        out_dir = os.path.dirname(output_file)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _apply_georef_from_json(self, georef_file: str, edited_file: str, output_file: str) -> None:
        if not os.path.isfile(georef_file):
            raise FileNotFoundError(f"坐标文件不存在：{georef_file}")
        if not os.path.isfile(edited_file):
            raise FileNotFoundError(f"编辑后的 TIFF 不存在：{edited_file}")

        with open(georef_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("format") != "geomosaic_georef_v1":
            raise ValueError("坐标文件格式不受支持：请使用本工具导出的 TXT/GEO 文件。")

        out_dir = os.path.dirname(output_file)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        # 先复制一份，再在副本上写入地理参考信息，避免破坏原始编辑文件
        shutil.copy2(edited_file, output_file)

        ds = gdal.Open(output_file, gdal.GA_Update)
        if ds is None:
            raise RuntimeError("无法以更新模式打开输出 TIFF（GDAL.Open 返回 None）。")

        try:
            gt = data.get("geotransform")
            if gt:
                ds.SetGeoTransform(tuple(gt))

            proj = data.get("projection_wkt") or ""
            if proj:
                ds.SetProjection(proj)

            gcps_data = data.get("gcps") or []
            gcp_proj = data.get("gcp_projection_wkt") or ""
            if gcps_data:
                gcp_list = []
                for g in gcps_data:
                    gcp_list.append(
                        gdal.GCP(
                            float(g.get("x", 0.0)),
                            float(g.get("y", 0.0)),
                            float(g.get("z", 0.0)),
                            float(g.get("pixel", 0.0)),
                            float(g.get("line", 0.0)),
                            str(g.get("id", "")),
                            str(g.get("info", "")),
                        )
                    )
                ds.SetGCPs(gcp_list, gcp_proj)

            # 元数据按需写回（可选）；默认不覆盖，避免写入不期望的内容
            # meta = data.get("metadata") or {}
            # if meta:
            #     ds.SetMetadata(meta)

            ds.FlushCache()
        finally:
            ds = None


# --- 运行主程序 ---
if __name__ == "__main__":
    root = tk.Tk()
    app = GeoTiffToolApp(root)
    root.mainloop()
