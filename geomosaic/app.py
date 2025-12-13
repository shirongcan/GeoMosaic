from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from geomosaic.backend import (
    RasterPreviewInfo,
    environment_hint,
    generate_xyz_tiles,
    guess_xyz_tiles_url_template,
    warp_to_web_mercator,
)
from geomosaic.html import PreviewConfig, build_leaflet_preview_html


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GeoMosaic - GeoTIFF → XYZ + 预览")
        self.geometry("860x560")

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_preview: RasterPreviewInfo | None = None

        self._build_ui()
        self._pump_logs()

        self._log(environment_hint())

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        frm = tk.Frame(self)
        frm.pack(fill=tk.X, **pad)

        # Input file
        tk.Label(frm, text="输入 GeoTIFF (.tif/.tiff)：").grid(row=0, column=0, sticky="w")
        self.in_var = tk.StringVar()
        tk.Entry(frm, textvariable=self.in_var, width=70).grid(row=0, column=1, sticky="we", padx=6)
        tk.Button(frm, text="浏览...", command=self._pick_input).grid(row=0, column=2)

        # Output folder
        tk.Label(frm, text="输出目录：").grid(row=1, column=0, sticky="w")
        self.out_var = tk.StringVar()
        tk.Entry(frm, textvariable=self.out_var, width=70).grid(row=1, column=1, sticky="we", padx=6)
        tk.Button(frm, text="选择...", command=self._pick_output).grid(row=1, column=2)

        # Zoom range
        zfrm = tk.Frame(self)
        zfrm.pack(fill=tk.X, **pad)

        tk.Label(zfrm, text="Min Zoom：").grid(row=0, column=0, sticky="w")
        self.minz_var = tk.IntVar(value=0)
        tk.Spinbox(zfrm, from_=0, to=30, width=6, textvariable=self.minz_var).grid(row=0, column=1, sticky="w")

        tk.Label(zfrm, text="Max Zoom：").grid(row=0, column=2, sticky="w", padx=(14, 0))
        self.maxz_var = tk.IntVar(value=18)
        tk.Spinbox(zfrm, from_=0, to=30, width=6, textvariable=self.maxz_var).grid(row=0, column=3, sticky="w")

        self.suggest_lbl = tk.Label(zfrm, text="建议 Max Zoom：-", fg="#555")
        self.suggest_lbl.grid(row=0, column=4, sticky="w", padx=(14, 0))

        self.keep_tmp_var = tk.BooleanVar(value=False)
        tk.Checkbutton(zfrm, text="保留中间文件(warped_3857.tif)", variable=self.keep_tmp_var).grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(4, 0)
        )

        # Buttons
        bfrm = tk.Frame(self)
        bfrm.pack(fill=tk.X, **pad)

        self.run_btn = tk.Button(bfrm, text="开始切片", command=self._start)
        self.run_btn.pack(side=tk.LEFT)

        self.open_btn = tk.Button(bfrm, text="打开输出目录", command=self._open_output, state=tk.DISABLED)
        self.open_btn.pack(side=tk.LEFT, padx=(10, 0))

        # Logs
        self.logbox = ScrolledText(self, height=22)
        self.logbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.logbox.configure(state=tk.DISABLED)

        # Resize behavior
        frm.columnconfigure(1, weight=1)

    def _pick_input(self) -> None:
        p = filedialog.askopenfilename(
            title="选择 GeoTIFF",
            filetypes=[
                ("GeoTIFF", "*.tif;*.tiff"),
                ("All files", "*.*"),
            ],
        )
        if p:
            self.in_var.set(p)

    def _pick_output(self) -> None:
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.out_var.set(p)

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("提示", "正在处理，请稍候...")
            return

        src = Path(self.in_var.get().strip())
        out_dir = Path(self.out_var.get().strip())
        minz = int(self.minz_var.get())
        maxz = int(self.maxz_var.get())

        if not src.exists():
            messagebox.showerror("错误", "请选择有效的输入 GeoTIFF 文件")
            return
        if not out_dir.exists():
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"无法创建输出目录：{e}")
                return

        self.open_btn.configure(state=tk.DISABLED)
        self.run_btn.configure(state=tk.DISABLED)
        self.suggest_lbl.configure(text="建议 Max Zoom：-", fg="#555")

        self._log("=" * 60)
        self._log(f"输入：{src}")
        self._log(f"输出：{out_dir}")
        self._log(f"Zoom：{minz}-{maxz}")

        self._worker = threading.Thread(
            target=self._run_pipeline,
            args=(src, out_dir, minz, maxz),
            daemon=True,
        )
        self._worker.start()

    def _run_pipeline(self, src: Path, out_dir: Path, minz: int, maxz: int) -> None:
        try:
            info = warp_to_web_mercator(src, out_dir, self._log)
            self._last_preview = info

            # Update suggestion label
            if info.suggested_max_zoom is not None:
                self._log_queue.put(f"__SUGGEST__{info.suggested_max_zoom}")

            generate_xyz_tiles(info.warped_path, out_dir, minz, maxz, self._log)

            self._log("生成 Leaflet 预览页面 index.html...")
            tiles_tpl, sample_tile = guess_xyz_tiles_url_template(out_dir)
            self._log(f"预览瓦片路径模板：{tiles_tpl}")
            if sample_tile is not None:
                try:
                    rel = sample_tile.relative_to(out_dir)
                except Exception:
                    rel = sample_tile
                self._log(f"示例瓦片文件：{rel}")
            cfg = PreviewConfig(
                title=src.name,
                min_zoom=minz,
                max_zoom=maxz,
                center_lat=info.center_lat,
                center_lng=info.center_lng,
                bounds_sw_lat=info.bounds_sw_lat,
                bounds_sw_lng=info.bounds_sw_lng,
                bounds_ne_lat=info.bounds_ne_lat,
                bounds_ne_lng=info.bounds_ne_lng,
                tiles_url_template=tiles_tpl,
            )
            (out_dir / "index.html").write_text(build_leaflet_preview_html(cfg), encoding="utf-8")

            # 默认清理中间文件，保持输出目录干净
            if not bool(self.keep_tmp_var.get()):
                try:
                    info.warped_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                try:
                    cache_dir = info.warped_path.parent
                    # 只在空目录时删除
                    cache_dir.rmdir()
                except Exception:
                    pass

            self._log("完成。你可以打开输出目录里的 index.html 预览。")
            self._log_queue.put("__DONE__")
        except Exception as e:
            self._log(f"错误：{e}")
            self._log_queue.put(f"__ERR__{e}")

    def _pump_logs(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()

                if msg.startswith("__DONE__"):
                    self.run_btn.configure(state=tk.NORMAL)
                    self.open_btn.configure(state=tk.NORMAL)
                    messagebox.showinfo("完成", "切片完成！已生成 index.html，可打开输出目录预览。")
                    continue

                if msg.startswith("__ERR__"):
                    self.run_btn.configure(state=tk.NORMAL)
                    self.open_btn.configure(state=tk.NORMAL)
                    messagebox.showerror("失败", msg[len("__ERR__") :])
                    continue

                if msg.startswith("__SUGGEST__"):
                    val = msg[len("__SUGGEST__") :]
                    self.suggest_lbl.configure(text=f"建议 Max Zoom：{val}", fg="#2a6")
                    continue

                self._append_log(msg)
        except queue.Empty:
            pass

        self.after(120, self._pump_logs)

    def _append_log(self, s: str) -> None:
        self.logbox.configure(state=tk.NORMAL)
        self.logbox.insert(tk.END, time.strftime("%H:%M:%S ") + s + "\n")
        self.logbox.see(tk.END)
        self.logbox.configure(state=tk.DISABLED)

    def _log(self, s: str) -> None:
        self._log_queue.put(s)

    def _open_output(self) -> None:
        p = self.out_var.get().strip()
        if not p:
            return
        try:
            import os

            os.startfile(p)  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showerror("错误", f"无法打开输出目录：{e}")


def main() -> int:
    app = App()
    app.mainloop()
    return 0
