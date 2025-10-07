#!/usr/bin/env python3
"""
Drag-and-drop Image Converter for Windows/Mac/Linux
- Drop multiple image files (e.g., .webp, .png, .jpg, .jpeg) to queue them.
- Choose output format (JPG/JPEG/PNG/WebP/TIFF/BMP) and options.
- Click "Convert" to export to an output folder.
- Then drop a destination folder on the MOVE area to move converted files there.

Dependencies:
  pip install pillow tkinterdnd2

Note: On some systems you may need the tkdnd DLL that comes with tkinterdnd2.
"""

import sys
import threading
import shutil
from pathlib import Path
from typing import List

try:
    # Tk base
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception as e:
    print("Tkinter is required but not available:", e)
    sys.exit(1)

# Drag-and-drop support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception as e:
    DND_FILES = None
    TkinterDnD = None

# Imaging
try:
    from PIL import Image
except Exception as e:
    print("Pillow (PIL) is required. Install with: pip install pillow")
    sys.exit(1)


SUPPORTED_INPUTS = {".webp", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".heic"}
# Output choices (JPG is a user-facing alias for Pillow's JPEG encoder)
OUTPUT_FORMATS = ["JPG", "JPEG", "PNG", "WEBP", "TIFF", "BMP"]

def is_image_file(p: Path) -> bool:
    return p.suffix.lower() in SUPPORTED_INPUTS and p.is_file()

def normalize_dnd_paths(widget, data: str) -> List[Path]:
    """Turn a Tk DND_FILES payload into a list of Path objects."""
    try:
        parts = widget.splitlist(data)  # handles brace-wrapped Windows paths
    except Exception:
        parts = data.split()
    paths: List[Path] = []
    for part in parts:
        part = str(part).strip("{}")
        p = Path(part)
        if p.exists():
            paths.append(p)
    return paths

def _resolve_output_fmt(fmt: str):
    """
    Map UI format to (pil_format, extension, is_jpeg_bool).
    - 'JPG'  -> ('JPEG', 'jpg',  True)
    - 'JPEG' -> ('JPEG', 'jpeg', True)
    - others -> (UPPER, lower,  False)
    """
    f = fmt.upper()
    if f == "JPG":
        return ("JPEG", "jpg", True)
    if f == "JPEG":
        return ("JPEG", "jpeg", True)
    return (f, f.lower(), False)

def export_image(src: Path, out_dir: Path, fmt: str, quality: int, keep_exif: bool, suffix: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    pil_fmt, out_ext, is_jpeg = _resolve_output_fmt(fmt)
    stem = src.stem
    out_name = f"{stem}{suffix}.{out_ext}"
    out_path = out_dir / out_name

    with Image.open(src) as im:
        save_kwargs = {}
        if is_jpeg:
            # JPEG doesn't support alpha; flatten transparent images onto white
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                im_to_save = Image.alpha_composite(background, rgba).convert("RGB")
            else:
                im_to_save = im.convert("RGB")
            save_kwargs["quality"] = quality
            save_kwargs["optimize"] = True
            save_kwargs["progressive"] = True
        elif pil_fmt in ("PNG", "TIFF", "WEBP", "BMP"):
            im_to_save = im
            if pil_fmt == "WEBP":
                save_kwargs["quality"] = quality
                save_kwargs["method"] = 6
                save_kwargs["lossless"] = False
            if pil_fmt == "PNG" and im.mode == "P":
                im_to_save = im.convert("RGBA")
        else:
            # Fallback
            im_to_save = im

        if keep_exif and "exif" in im.info:
            save_kwargs["exif"] = im.info["exif"]

        im_to_save.save(out_path, pil_fmt, **save_kwargs)

    return out_path


class App(TkinterDnD.Tk if TkinterDnD else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Drag-and-Drop Image Converter")
        self.geometry("840x540")
        self.minsize(720, 480)

        self.queue: List[Path] = []
        self.converted_paths: List[Path] = []

        self._build_ui()
        self._wire_dnd()

    def _build_ui(self):
        # Top controls frame
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        # Output format
        ttk.Label(top, text="Output format:").grid(row=0, column=0, sticky="w")
        self.format_var = tk.StringVar(value="JPG")
        self.format_cb = ttk.Combobox(top, textvariable=self.format_var, values=OUTPUT_FORMATS, state="readonly", width=10)
        self.format_cb.grid(row=0, column=1, padx=(6, 18), sticky="w")

        # Quality
        ttk.Label(top, text="Quality (JPG/JPEG/WEBP):").grid(row=0, column=2, sticky="w")
        self.quality_var = tk.IntVar(value=90)
        self.quality_scale = ttk.Scale(top, from_=50, to=100, orient="horizontal", command=lambda v: self._update_quality_label())
        self.quality_scale.set(self.quality_var.get())
        self.quality_scale.grid(row=0, column=3, sticky="we", padx=(6, 6))
        self.quality_label = ttk.Label(top, text="90")
        self.quality_label.grid(row=0, column=4, sticky="w")

        # Keep EXIF
        self.exif_var = tk.BooleanVar(value=True)
        exif_cb = ttk.Checkbutton(top, text="Keep EXIF/metadata when possible", variable=self.exif_var)
        exif_cb.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # Suffix
        ttk.Label(top, text="Filename suffix (optional):").grid(row=1, column=3, sticky="e", padx=(6, 6))
        self.suffix_var = tk.StringVar(value="_converted")
        self.suffix_entry = ttk.Entry(top, textvariable=self.suffix_var, width=18)
        self.suffix_entry.grid(row=1, column=4, sticky="w")

        # Output directory
        outf = ttk.Frame(self, padding=(10, 0, 10, 0))
        outf.pack(fill="x", pady=(6, 0))
        ttk.Label(outf, text="Output folder:").pack(anchor="w")
        row = ttk.Frame(outf)
        row.pack(fill="x")
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "converted_output"))
        self.output_entry = ttk.Entry(row, textvariable=self.output_dir_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose…", command=self.choose_output_dir).pack(side="left", padx=(8, 0))

        # Queue frame with drop zone
        mid = ttk.Frame(self, padding=10)
        mid.pack(fill="both", expand=True)

        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        ttk.Label(left, text="1) Drop image files here (or Add Files)…").pack(anchor="w")
        self.drop_area = tk.Text(left, height=8, relief="solid", borderwidth=1)
        self.drop_area.insert("end", "Drop files here…")
        # Keep in normal state for tkdnd on macOS; make read-only via bindings
        self.drop_area.pack(fill="both", expand=True, pady=(4, 8))
        for seq in ("<Key>", "<Button-1>", "<Button-2>", "<Button-3>"):
            self.drop_area.bind(seq, lambda e: "break")

        btns = ttk.Frame(left)
        btns.pack(fill="x")
        ttk.Button(btns, text="Add Files…", command=self.add_files_dialog).pack(side="left")
        ttk.Button(btns, text="Clear List", command=self.clear_queue).pack(side="left", padx=8)

        # Listbox to show queued files
        self.queue_list = tk.Listbox(left, height=8, activestyle="dotbox", selectmode="extended")
        self.queue_list.pack(fill="both", expand=True)
        ttk.Button(left, text="Remove Selected", command=self.remove_selected).pack(anchor="w", pady=(6, 0))

        # Right panel
        right = ttk.Frame(mid, width=260)
        right.pack(side="left", fill="y")

        # Convert controls
        self.convert_btn = ttk.Button(right, text="Convert ▶", command=self.convert_now)
        self.convert_btn.pack(fill="x", pady=(4, 4))
        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.pack(fill="x")
        try:
            tk_ver = self.tk.call("info", "patchlevel")
        except Exception:
            tk_ver = "unknown"
        self.status_var = tk.StringVar(value=f"Ready. Tk {tk_ver}")
        ttk.Label(right, textvariable=self.status_var, wraplength=240, justify="left").pack(fill="x", pady=(6, 12))

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(8, 8))

        # Move area
        ttk.Label(right, text="2) Move converted files").pack(anchor="w")
        self.move_info = ttk.Label(right, text="Drop a folder onto the box below to MOVE the newly converted files there.\n(Or click 'Choose Folder…')", wraplength=240, justify="left")
        self.move_info.pack(anchor="w", pady=(2, 6))
        self.move_drop = tk.Text(right, height=4, relief="solid", borderwidth=1)
        self.move_drop.insert("end", "Drop destination folder here…")
        # Keep in normal state for tkdnd on macOS; make read-only via bindings
        self.move_drop.pack(fill="x")
        for seq in ("<Key>", "<Button-1>", "<Button-2>", "<Button-3>"):
            self.move_drop.bind(seq, lambda e: "break")

        ttk.Button(right, text="Choose Folder…", command=self.move_choose_folder).pack(fill="x", pady=(8, 0))

        # Footer
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        ttk.Label(footer, text="Tips: You can drop files in any order. Output names get a suffix to avoid overwriting.").pack(anchor="w")

        # Grid config
        top.columnconfigure(3, weight=1)

    def _wire_dnd(self):
        if TkinterDnD:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind("<<Drop>>", self.on_drop_files)
            self.move_drop.drop_target_register(DND_FILES)
            self.move_drop.dnd_bind("<<Drop>>", self.on_drop_move_folder)
        else:
            self.status_var.set("Drag-and-drop not available (install tkinterdnd2). Use the 'Add Files…' and 'Choose…' buttons.")
    
    # --- UI Actions ---
    def _update_quality_label(self):
        self.quality_var.set(int(float(self.quality_scale.get())))
        self.quality_label.config(text=str(self.quality_var.get()))

    def choose_output_dir(self):
        chosen = filedialog.askdirectory(title="Choose Output Folder")
        if chosen:
            self.output_dir_var.set(chosen)

    def add_files_dialog(self):
        filetypes = [
            ("Images", "*.webp *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.heic"),
            ("All files", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="Select Images", filetypes=filetypes)
        self._add_paths([Path(p) for p in paths])

    def clear_queue(self):
        self.queue.clear()
        self.queue_list.delete(0, "end")
        self.status_var.set("Cleared list.")

    def remove_selected(self):
        sel = list(self.queue_list.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            try:
                self.queue.pop(idx)
                self.queue_list.delete(idx)
            except Exception:
                pass
        self.status_var.set(f"Removed {len(sel)} item(s).")

    def on_drop_files(self, event):
        print("Drop event (files):", repr(getattr(event, "data", None)))
        paths = normalize_dnd_paths(self.drop_area, event.data)
        files: List[Path] = []
        for p in paths:
            if p.is_dir():
                for ext in SUPPORTED_INPUTS:
                    files.extend(p.rglob(f"*{ext}"))
            elif is_image_file(p):
                files.append(p)
        if not files:
            messagebox.showinfo("No images found", "The dropped items didn't include any supported images.")
            return
        self._add_paths(files)

    def _add_paths(self, files: List[Path]):
        new_files = [p for p in files if is_image_file(p)]
        if not new_files:
            return
        existing = {str(p.resolve()) for p in self.queue}
        added = 0
        for p in new_files:
            rp = str(p.resolve())
            if rp not in existing:
                self.queue.append(p)
                self.queue_list.insert("end", str(p))
                existing.add(rp)
                added += 1
        self.status_var.set(f"Added {added} file(s). Total in queue: {len(self.queue)}.")

    def on_drop_move_folder(self, event):
        print("Drop event (move folder):", repr(getattr(event, "data", None)))
        paths = normalize_dnd_paths(self.move_drop, event.data)
        if not paths:
            return
        dest = None
        for p in paths:
            if p.is_dir():
                dest = p
                break
        if not dest:
            messagebox.showerror("Not a folder", "Please drop a destination folder.")
            return
        self._move_converted_to(dest)

    def move_choose_folder(self):
        chosen = filedialog.askdirectory(title="Choose destination to MOVE converted files")
        if chosen:
            self._move_converted_to(Path(chosen))

    def _move_converted_to(self, dest: Path):
        if not self.converted_paths:
            messagebox.showinfo("Nothing to move", "Convert some files first, then try moving them.")
            return
        moved = 0
        dest.mkdir(parents=True, exist_ok=True)
        for src in list(self.converted_paths):
            try:
                target = dest / src.name
                if target.exists():
                    stem = src.stem
                    ext = src.suffix
                    i = 1
                    while True:
                        candidate = dest / f"{stem} ({i}){ext}"
                        if not candidate.exists():
                            target = candidate
                            break
                        i += 1
                shutil.move(str(src), str(target))
                moved += 1
                self.converted_paths.remove(src)
            except Exception as e:
                print("Move failed:", e)
        self.status_var.set(f"Moved {moved} file(s) to: {dest}")
        if moved:
            messagebox.showinfo("Move complete", f"Moved {moved} file(s) to:\n{dest}")

    def convert_now(self):
        if not self.queue:
            messagebox.showinfo("No files", "Add or drop some images first.")
            return
        out_dir = Path(self.output_dir_var.get())
        fmt = self.format_var.get().upper()
        quality = int(self.quality_var.get())
        keep_exif = bool(self.exif_var.get())
        suffix = self.suffix_var.get().strip()

        if fmt not in (f.upper() for f in OUTPUT_FORMATS):
            messagebox.showerror("Unsupported format", f"{fmt} is not supported.")
            return

        self.convert_btn.config(state="disabled")
        self.progress.config(mode="determinate", value=0, maximum=len(self.queue))
        self.status_var.set("Converting…")
        self.converted_paths.clear()

        def worker():
            successes = 0
            failures = 0
            for idx, src in enumerate(list(self.queue)):
                try:
                    out_path = export_image(src, out_dir, fmt, quality, keep_exif, suffix)
                    self.converted_paths.append(out_path)
                    successes += 1
                except Exception as e:
                    print(f"Failed: {src} -> {e}")
                    failures += 1
                finally:
                    self.progress.after(0, lambda v=idx+1: self.progress.config(value=v))
            def done():
                self.convert_btn.config(state="normal")
                self.status_var.set(f"Done. Converted {successes} file(s), {failures} failed. Output: {out_dir}")
                messagebox.showinfo("Conversion complete", f"Converted {successes} file(s), {failures} failed.\n\nOutput folder:\n{out_dir}\n\nNext: drop a folder onto the MOVE box to move them.")
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
