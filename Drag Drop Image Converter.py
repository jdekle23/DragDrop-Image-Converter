#!/usr/bin/env python3
"""
Drag-and-drop Image Converter for Windows/Mac/Linux
- Drop multiple image files (e.g., .webp, .png, .jpg, .jpeg, .hif, .heic, .heif) to queue them.
- Choose output format (JPG/JPEG/PNG/WebP/TIFF/BMP/HEIF) and options.
- Click "Convert" to export to an output folder.
- Then drop a destination folder on the MOVE area to move converted files there.

Dependencies:
  pip install pillow pillow-heif tkinterdnd2

Note: On some systems you may need the tkdnd DLL that comes with tkinterdnd2.
"""

import sys
import threading
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Iterable, Tuple

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
except Exception:
    DND_FILES = None
    TkinterDnD = None

# Imaging
try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except Exception:
    print("Pillow (PIL) is required. Install with: pip install pillow")
    sys.exit(1)

# Optional: HEIC/HEIF/HIF support (won't crash if not present)
try:
    import pillow_heif  # noqa: F401
    from pillow_heif import register_heif_opener, register_heif_writer

    register_heif_opener()
    register_heif_writer()
except Exception:
    pass

SUPPORTED_INPUTS = {
    ".webp", ".png", ".jpg", ".jpeg", ".bmp",
    ".tif", ".tiff", ".gif",
    ".heic", ".heif", ".hif",
}

# Output choices (JPG is a user-facing alias for Pillow's JPEG encoder)
# Added HEIF so you can keep HDR/ICC profiles when desired.
OUTPUT_FORMATS = ["JPG", "JPEG", "PNG", "WEBP", "TIFF", "BMP", "HEIF"]

# Upscale choices exposed in the UI. Map label -> scale factor (float multiplier).
UPSCALE_OPTIONS = {
    "No Upscale (100%)": 1.0,
    "125% (1.25×)": 1.25,
    "150% (1.5×)": 1.5,
    "200% (2×)": 2.0,
}

# Pillow 10+ uses Image.Resampling; older versions have constants directly on Image
_RESAMPLE_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


@dataclass(frozen=True)
class EnhancementSettings:
    autopilot: bool = False
    adjust_lighting: bool = False
    balance_color: bool = False
    sharpen_subject: bool = False
    preserve_text: bool = False
    denoise: bool = False

    # Tunable intensities
    adjust_lighting_amt: float = 1.0   # brightness multiplier (1.00–1.30)
    balance_color_amt: float = 1.0     # saturation multiplier (1.00–1.30)
    sharpen_amt: float = 1.0           # sharpness factor (1.00–2.00)
    denoise_level: int = 0             # 0(off), 1(size=3), 2(size=5), 3(size=7)

    def any_enabled(self) -> bool:
        return any((
            self.autopilot,
            self.adjust_lighting and self.adjust_lighting_amt != 1.0,
            self.balance_color and self.balance_color_amt != 1.0,
            self.sharpen_subject and self.sharpen_amt != 1.0,
            self.preserve_text,
            self.denoise and self.denoise_level > 0,
        ))


def is_image_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED_INPUTS


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


def _resolve_output_fmt(fmt: str) -> Tuple[str, str, bool]:
    """
    Map UI format to (pil_format, extension, is_jpeg_bool).
    - 'JPG'  -> ('JPEG', 'jpg',  True)
    - 'JPEG' -> ('JPEG', 'jpeg', True)
    - 'HEIF' -> ('HEIF', 'heif', False)
    - others -> (UPPER, lower,  False)
    """
    f = fmt.upper()
    if f == "JPG":
        return ("JPEG", "jpg", True)
    if f == "JPEG":
        return ("JPEG", "jpeg", True)
    if f == "HEIF":
        return ("HEIF", "heif", False)
    return (f, f.lower(), False)


def _apply_upscale(im: Image.Image, scale: float) -> Image.Image:
    """Return an upscaled copy of *im* when scale > 1. Uses high-quality Lanczos."""
    if scale <= 1.0:
        return im
    w, h = im.size
    new_size = (int(round(w * scale)), int(round(h * scale)))
    if new_size == im.size:
        return im
    return im.resize(new_size, _RESAMPLE_LANCZOS)


def _extract_alpha(im: Image.Image):
    """Return (base_image_without_alpha, alpha_channel_or_None, original_mode)."""
    original_mode = im.mode
    alpha = None
    base = im
    if im.mode in ("RGBA", "LA"):
        alpha = im.getchannel("A")
        base = im.convert("RGB" if im.mode == "RGBA" else "L")
    elif im.mode == "P":
        rgba = im.convert("RGBA")
        if "transparency" in im.info:
            alpha = rgba.getchannel("A")
        base = rgba.convert("RGB")
    elif im.mode not in ("RGB", "L"):
        base = im.convert("RGB")
    return base, alpha, original_mode


def _recombine_alpha(base: Image.Image, alpha, original_mode: str) -> Image.Image:
    if alpha is None:
        if original_mode == "L" and base.mode != "L":
            return base.convert("L")
        return base
    if original_mode == "LA":
        l = base.convert("L")
        return Image.merge("LA", (l, alpha))
    rgb = base.convert("RGB")
    rgba = rgb.copy()
    rgba.putalpha(alpha)
    return rgba


def _enhance_autopilot(im: Image.Image) -> Image.Image:
    work = ImageOps.autocontrast(im, cutoff=1)
    if work.mode == "RGB":
        work = ImageEnhance.Color(work).enhance(1.08)
    work = ImageEnhance.Sharpness(work).enhance(1.12)
    work = ImageEnhance.Contrast(work).enhance(1.05)
    return work


def _enhance_adjust_lighting(im: Image.Image, amt: float) -> Image.Image:
    """Brightness multiplier ~1.00–1.30; mild autocontrast first."""
    amt = max(0.8, min(1.3, float(amt)))
    work = ImageOps.autocontrast(im, cutoff=1)
    if work.mode == "RGB" and abs(amt - 1.0) > 1e-3:
        work = ImageEnhance.Brightness(work).enhance(amt)
    return work


def _enhance_balance_color(im: Image.Image, amt: float) -> Image.Image:
    """Saturation multiplier ~1.00–1.30; channel-wise autocontrast first."""
    amt = max(0.8, min(1.3, float(amt)))
    if im.mode != "RGB":
        return ImageOps.autocontrast(im, cutoff=1)
    r, g, b = im.split()
    r = ImageOps.autocontrast(r, cutoff=1)
    g = ImageOps.autocontrast(g, cutoff=1)
    b = ImageOps.autocontrast(b, cutoff=1)
    merged = Image.merge("RGB", (r, g, b))
    if abs(amt - 1.0) > 1e-3:
        merged = ImageEnhance.Color(merged).enhance(amt)
    return merged


def _enhance_sharpen(im: Image.Image, factor: float) -> Image.Image:
    """Sharpness factor 1.0–2.0 (1.0 = no change)."""
    factor = max(0.5, min(2.0, float(factor)))
    return ImageEnhance.Sharpness(im).enhance(factor)


def _enhance_preserve_text(im: Image.Image) -> Image.Image:
    work = ImageEnhance.Contrast(im).enhance(1.35)
    work = ImageEnhance.Brightness(work).enhance(1.08)
    return work.filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=4))


def _enhance_denoise(im: Image.Image, level: int) -> Image.Image:
    level = int(max(0, min(3, level)))
    if level <= 0:
        return im
    size = 3 + 2 * (level - 1)  # 1->3, 2->5, 3->7
    return im.filter(ImageFilter.MedianFilter(size=size))


def apply_enhancements(im: Image.Image, settings: EnhancementSettings) -> Image.Image:
    if not settings or not settings.any_enabled():
        return im

    base, alpha, original_mode = _extract_alpha(im)
    work = base

    if settings.autopilot:
        work = _enhance_autopilot(work)

    if settings.adjust_lighting and settings.adjust_lighting_amt != 1.0:
        work = _enhance_adjust_lighting(work, settings.adjust_lighting_amt)

    if settings.balance_color and settings.balance_color_amt != 1.0:
        work = _enhance_balance_color(work, settings.balance_color_amt)

    if settings.denoise and settings.denoise_level > 0:
        work = _enhance_denoise(work, settings.denoise_level)

    if settings.sharpen_subject and settings.sharpen_amt != 1.0:
        work = _enhance_sharpen(work, settings.sharpen_amt)

    if settings.preserve_text:
        work = _enhance_preserve_text(work)

    return _recombine_alpha(work, alpha, original_mode)


def export_image(
    src: Path,
    out_dir: Path,
    fmt: str,
    quality: int,
    keep_exif: bool,
    suffix: str,
    scale: float,
    enhancements: EnhancementSettings,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    pil_fmt, out_ext, is_jpeg = _resolve_output_fmt(fmt)
    stem = src.stem
    out_name = f"{stem}{suffix}.{out_ext}"
    out_path = out_dir / out_name

    with Image.open(src) as im:
        save_kwargs = {}

        # Grab metadata up front so we can re-attach it later
        exif_data = im.info.get("exif")
        icc_profile = im.info.get("icc_profile")

        # Prepare base image depending on output format
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
        elif pil_fmt in ("PNG", "TIFF", "WEBP", "BMP", "HEIF"):
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

        # Upscale if requested
        if scale and scale > 1.0:
            im_to_save = _apply_upscale(im_to_save, scale)

        # Apply enhancements
        if enhancements and enhancements.any_enabled():
            im_to_save = apply_enhancements(im_to_save, enhancements)

        # Preserve EXIF if requested
        if keep_exif and exif_data:
            save_kwargs["exif"] = exif_data

        # Preserve ICC color profile when present (important for HIF/HEIF HDR images)
        if icc_profile and pil_fmt in ("JPEG", "PNG", "TIFF", "WEBP", "HEIF"):
            save_kwargs["icc_profile"] = icc_profile

        im_to_save.save(out_path, pil_fmt, **save_kwargs)

    return out_path


class App(TkinterDnD.Tk if TkinterDnD else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Drag-and-Drop Image Converter")
        self.geometry("840x600")
        self.minsize(720, 520)

        self.queue: List[Path] = []
        self.converted_paths: List[Path] = []

        self._build_ui()
        self._wire_dnd()

    # ---------------- UI ----------------
    def _build_ui(self):
        # Top controls frame
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        # Output format
        ttk.Label(top, text="Output format:").grid(row=0, column=0, sticky="w")
        self.format_var = tk.StringVar(value="JPG")
        self.format_cb = ttk.Combobox(
            top,
            textvariable=self.format_var,
            values=OUTPUT_FORMATS,
            state="readonly",
            width=10,
        )
        self.format_cb.grid(row=0, column=1, padx=(6, 18), sticky="w")

        # Quality
        ttk.Label(top, text="Quality (JPG/JPEG/WEBP):").grid(row=0, column=2, sticky="w")
        self.quality_var = tk.IntVar(value=90)
        self.quality_label = ttk.Label(top, text=str(self.quality_var.get()))
        self.quality_label.grid(row=0, column=4, sticky="w")
        self.quality_scale = ttk.Scale(
            top,
            from_=50,
            to=100,
            orient="horizontal",
            command=lambda v: self._update_quality_label(),
        )
        self.quality_scale.grid(row=0, column=3, sticky="we", padx=(6, 6))
        self.quality_scale.set(self.quality_var.get())

        # Keep EXIF
        self.exif_var = tk.BooleanVar(value=True)
        exif_cb = ttk.Checkbutton(
            top,
            text="Keep EXIF/metadata when possible",
            variable=self.exif_var,
        )
        exif_cb.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # Suffix
        ttk.Label(top, text="Filename suffix (optional):").grid(
            row=1, column=3, sticky="e", padx=(6, 6)
        )
        self.suffix_var = tk.StringVar(value="_converted")
        self.suffix_entry = ttk.Entry(top, textvariable=self.suffix_var, width=18)
        self.suffix_entry.grid(row=1, column=4, sticky="w")

        # Upscale selector
        ttk.Label(top, text="Upscale:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.upscale_var = tk.StringVar(value=list(UPSCALE_OPTIONS.keys())[0])
        self.upscale_cb = ttk.Combobox(
            top,
            textvariable=self.upscale_var,
            values=list(UPSCALE_OPTIONS.keys()),
            state="readonly",
            width=18,
        )
        self.upscale_cb.grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(6, 0), pady=(8, 0)
        )

        # Output directory
        outf = ttk.Frame(self, padding=(10, 0, 10, 0))
        outf.pack(fill="x", pady=(6, 0))
        ttk.Label(outf, text="Output folder:").pack(anchor="w")
        row = ttk.Frame(outf)
        row.pack(fill="x")
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "converted_output"))
        self.output_entry = ttk.Entry(row, textvariable=self.output_dir_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose…", command=self.choose_output_dir).pack(
            side="left", padx=(8, 0)
        )

        # Queue frame with drop zone
        mid = ttk.Frame(self, padding=10)
        mid.pack(fill="both", expand=True)

        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        ttk.Label(left, text="1) Drop image files here (or Add Files)…").pack(anchor="w")
        self.drop_area = tk.Text(left, height=8, relief="solid", borderwidth=1)
        self.drop_area.insert("end", "Drop files here…")
        self.drop_area.pack(fill="both", expand=True, pady=(4, 8))
        for seq in ("<Key>", "<Button-1>", "<Button-2>", "<Button-3>"):
            self.drop_area.bind(seq, lambda e: "break")

        # Buttons under drop area
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(2, 6))
        ttk.Button(btns, text="Add Files…", command=self.add_files_dialog).pack(
            side="left"
        )
        ttk.Button(btns, text="Clear List", command=self.clear_queue).pack(
            side="left", padx=8
        )

        # Listbox to show queued files
        self.queue_list = tk.Listbox(
            left, height=8, activestyle="dotbox", selectmode="extended"
        )
        self.queue_list.pack(fill="both", expand=True)
        ttk.Button(left, text="Remove Selected", command=self.remove_selected).pack(
            anchor="w", pady=(6, 0)
        )

        # Right panel
        right = ttk.Frame(mid, width=280)
        right.pack(side="left", fill="y")

        # Convert controls
        self.convert_btn = ttk.Button(
            right, text="Convert ▶", command=self.convert_now
        )
        self.convert_btn.pack(fill="x", pady=(4, 4))
        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.pack(fill="x")
        try:
            tk_ver = self.tk.call("info", "patchlevel")
        except Exception:
            tk_ver = "unknown"
        self.status_var = tk.StringVar(value=f"Ready. Tk {tk_ver}")
        ttk.Label(
            right, textvariable=self.status_var, wraplength=260, justify="left"
        ).pack(fill="x", pady=(6, 12))

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(8, 8))

        enhancements = ttk.LabelFrame(
            right, text="Enhancements (customizable)", padding=(8, 6)
        )
        enhancements.pack(fill="x", pady=(0, 12))

        # --- Autopilot ---
        self.autopilot_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            enhancements,
            text="Autopilot",
            variable=self.autopilot_var,
            command=self._on_autopilot_toggle,
        ).pack(anchor="w")
        self.enhance_hint = ttk.Label(
            enhancements,
            text="Pick enhancements to run before export.",
            wraplength=240,
            justify="left",
        )
        self.enhance_hint.pack(fill="x", pady=(4, 6))

        # --- Adjust lighting (checkbox + slider 0–30%) ---
        self.adjust_lighting_var = tk.BooleanVar(value=False)
        row_light = ttk.Frame(enhancements)
        row_light.pack(fill="x", padx=(0, 0))
        ttk.Checkbutton(
            row_light,
            text="Adjust lighting",
            variable=self.adjust_lighting_var,
            command=lambda: self._toggle_enh_control("light"),
        ).pack(side="left")
        self.light_pct_var = tk.DoubleVar(value=6.0)
        self.light_scale = ttk.Scale(
            row_light,
            from_=0.0,
            to=30.0,
            orient="horizontal",
            command=lambda v: self._on_light_pct_changed(),
        )
        self.light_scale.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.light_label = ttk.Label(row_light, text="6%")
        self.light_label.pack(side="left")
        self._set_enh_control_state(self.light_scale, self.light_label, enabled=False)

        # --- Balance color (checkbox + slider 0–30%) ---
        self.balance_color_var = tk.BooleanVar(value=False)
        row_color = ttk.Frame(enhancements)
        row_color.pack(fill="x", padx=(0, 0), pady=(2, 0))
        ttk.Checkbutton(
            row_color,
            text="Balance color",
            variable=self.balance_color_var,
            command=lambda: self._toggle_enh_control("color"),
        ).pack(side="left")
        self.color_pct_var = tk.DoubleVar(value=6.0)
        self.color_scale = ttk.Scale(
            row_color,
            from_=0.0,
            to=30.0,
            orient="horizontal",
            command=lambda v: self._on_color_pct_changed(),
        )
        self.color_scale.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.color_label = ttk.Label(row_color, text="6%")
        self.color_label.pack(side="left")
        self._set_enh_control_state(self.color_scale, self.color_label, enabled=False)

        # --- Sharpen subject (checkbox + slider 0–100% -> 1.00–2.00) ---
        self.sharpen_var = tk.BooleanVar(value=False)
        row_sharp = ttk.Frame(enhancements)
        row_sharp.pack(fill="x", padx=(0, 0), pady=(2, 0))
        ttk.Checkbutton(
            row_sharp,
            text="Sharpen subject",
            variable=self.sharpen_var,
            command=lambda: self._toggle_enh_control("sharpen"),
        ).pack(side="left")
        self.sharp_pct_var = tk.DoubleVar(value=30.0)  # mild default
        self.sharp_scale = ttk.Scale(
            row_sharp,
            from_=0.0,
            to=100.0,
            orient="horizontal",
            command=lambda v: self._on_sharp_pct_changed(),
        )
        self.sharp_scale.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.sharp_label = ttk.Label(row_sharp, text="30%")
        self.sharp_label.pack(side="left")
        self._set_enh_control_state(self.sharp_scale, self.sharp_label, enabled=False)

        # --- Preserve text (checkbox only) ---
        self.preserve_text_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            enhancements, text="Preserve text", variable=self.preserve_text_var
        ).pack(anchor="w", padx=(0, 0))

        # --- Denoise (checkbox + levels 0–3) ---
        self.denoise_var = tk.BooleanVar(value=False)
        row_denoise = ttk.Frame(enhancements)
        row_denoise.pack(fill="x", padx=(0, 0), pady=(2, 0))
        ttk.Checkbutton(
            row_denoise,
            text="Denoise",
            variable=self.denoise_var,
            command=lambda: self._toggle_enh_control("denoise"),
        ).pack(side="left")
        self.denoise_level_var = tk.IntVar(value=1)  # default gentle (0=off)

        # Use tk.Scale (not ttk.Scale) for integer steps
        self.denoise_scale = tk.Scale(
            row_denoise,
            from_=0,
            to=3,
            orient="horizontal",
            resolution=1,
            showvalue=0,
            variable=self.denoise_level_var,
            command=lambda v: self._on_denoise_changed(),
            length=150,
        )
        self.denoise_scale.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.denoise_label = ttk.Label(row_denoise, text="Lvl 1")
        self.denoise_label.pack(side="left")
        self._set_enh_control_state(
            self.denoise_scale, self.denoise_label, enabled=False
        )

        # Move area
        ttk.Label(right, text="2) Move converted files").pack(anchor="w")
        self.move_info = ttk.Label(
            right,
            text=(
                "Drop a folder onto the box below to MOVE the newly converted files "
                "there.\n(Or click 'Choose Folder…')"
            ),
            wraplength=240,
            justify="left",
        )
        self.move_info.pack(anchor="w", pady=(2, 6))
        self.move_drop = tk.Text(right, height=4, relief="solid", borderwidth=1)
        self.move_drop.insert("end", "Drop destination folder here…")
        self.move_drop.pack(fill="x")
        for seq in ("<Key>", "<Button-1>", "<Button-2>", "<Button-3>"):
            self.move_drop.bind(seq, lambda e: "break")

        ttk.Button(right, text="Choose Folder…", command=self.move_choose_folder).pack(
            fill="x", pady=(8, 0)
        )

        # Footer
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        ttk.Label(
            footer,
            text=(
                "Tips: You can drop files in any order. Output names get a suffix to "
                "avoid overwriting."
            ),
        ).pack(anchor="w")

        # Grid config
        top.columnconfigure(3, weight=1)

    def _wire_dnd(self):
        if TkinterDnD:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind("<<Drop>>", self.on_drop_files)
            self.move_drop.drop_target_register(DND_FILES)
            self.move_drop.dnd_bind("<<Drop>>", self.on_drop_move_folder)
        else:
            self.status_var.set(
                "Drag-and-drop not available (install tkinterdnd2). "
                "Use the 'Add Files…' and 'Choose…' buttons."
            )

    # ------------- Helpers & Actions -------------
    def _update_quality_label(self):
        value = int(float(self.quality_scale.get()))
        self.quality_var.set(value)
        self.quality_label.config(text=str(value))

    def _set_enh_control_state(self, scale_widget, label_widget, enabled: bool):
        state = "normal" if enabled else "disabled"
        try:
            scale_widget.configure(state=state)
        except Exception:
            pass
        try:
            label_widget.configure(state=state)
        except Exception:
            pass

    def _toggle_enh_control(self, which: str):
        if which == "light":
            self._set_enh_control_state(
                self.light_scale, self.light_label, self.adjust_lighting_var.get()
            )
        elif which == "color":
            self._set_enh_control_state(
                self.color_scale, self.color_label, self.balance_color_var.get()
            )
        elif which == "sharpen":
            self._set_enh_control_state(
                self.sharp_scale, self.sharp_label, self.sharpen_var.get()
            )
        elif which == "denoise":
            self._set_enh_control_state(
                self.denoise_scale, self.denoise_label, self.denoise_var.get()
            )

    def _on_light_pct_changed(self):
        val = float(self.light_scale.get())
        self.light_pct_var.set(val)
        self.light_label.config(text=f"{val:.0f}%")

    def _on_color_pct_changed(self):
        val = float(self.color_scale.get())
        self.color_pct_var.set(val)
        self.color_label.config(text=f"{val:.0f}%")

    def _on_sharp_pct_changed(self):
        val = float(self.sharp_scale.get())
        self.sharp_pct_var.set(val)
        self.sharp_label.config(text=f"{val:.0f}%")

    def _on_denoise_changed(self):
        val = int(self.denoise_level_var.get())
        self.denoise_label.config(text=f"Lvl {val}")

    def _update_enhancement_hint(self):
        if getattr(self, "enhance_hint", None) is None:
            return
        if self.autopilot_var.get():
            text = (
                "Autopilot will gently tune lighting, color, and clarity. "
                "Use sliders to customize; Autopilot won't override your choices."
            )
        else:
            text = (
                "Pick individual enhancements and use sliders to fine-tune before "
                "exporting."
            )
        self.enhance_hint.config(text=text)

    def _on_autopilot_toggle(self):
        if self.autopilot_var.get():
            # softly enable core steps
            if not self.adjust_lighting_var.get():
                self.adjust_lighting_var.set(True)
                self._toggle_enh_control("light")
            if not self.balance_color_var.get():
                self.balance_color_var.set(True)
                self._toggle_enh_control("color")

            # set mild defaults only if sliders are 0
            if self.light_pct_var.get() == 0.0:
                self.light_scale.set(6.0)
                self._on_light_pct_changed()
            if self.color_pct_var.get() == 0.0:
                self.color_scale.set(6.0)
                self._on_color_pct_changed()
        self._update_enhancement_hint()

    def _collect_enhancement_settings(self) -> EnhancementSettings:
        # map percents → multipliers
        light_amt = (
            1.0 + (self.light_pct_var.get() / 100.0)
            if self.adjust_lighting_var.get()
            else 1.0
        )
        color_amt = (
            1.0 + (self.color_pct_var.get() / 100.0)
            if self.balance_color_var.get()
            else 1.0
        )

        # sharpen: 0–100% → 1.00–2.00
        sharp_factor = (
            1.0 + (self.sharp_pct_var.get() / 100.0)
            if self.sharpen_var.get()
            else 1.0
        )

        # denoise: 0–3 integer levels
        denoise_lvl = (
            int(self.denoise_level_var.get()) if self.denoise_var.get() else 0
        )

        return EnhancementSettings(
            autopilot=bool(self.autopilot_var.get()),
            adjust_lighting=bool(self.adjust_lighting_var.get()),
            balance_color=bool(self.balance_color_var.get()),
            sharpen_subject=bool(self.sharpen_var.get()),
            preserve_text=bool(self.preserve_text_var.get()),
            denoise=bool(self.denoise_var.get()),
            adjust_lighting_amt=light_amt,
            balance_color_amt=color_amt,
            sharpen_amt=sharp_factor,
            denoise_level=denoise_lvl,
        )

    def choose_output_dir(self):
        chosen = filedialog.askdirectory(title="Choose Output Folder")
        if chosen:
            self.output_dir_var.set(chosen)

    def add_files_dialog(self):
        filetypes = [
            (
                "Images",
                "*.webp *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.heic *.heif *.hif",
            ),
            ("All files", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="Select Images", filetypes=filetypes)
        self._add_paths([Path(p) for p in paths])

    def _add_paths(self, paths: Iterable[Path]):
        added = 0
        for p in paths:
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if is_image_file(child):
                        self.queue.append(child)
                        self.queue_list.insert("end", str(child))
                        added += 1
            else:
                if is_image_file(p):
                    self.queue.append(p)
                    self.queue_list.insert("end", str(p))
                    added += 1
        if added:
            self.status_var.set(f"Added {added} file(s).")
        else:
            self.status_var.set("No supported images found.")

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
                self.queue_list.delete(idx)
                del self.queue[idx]
            except Exception:
                pass
        self.status_var.set("Removed selected.")

    # -------- Drag & Drop handlers --------
    def on_drop_files(self, event):
        paths = normalize_dnd_paths(self, event.data)
        self._add_paths(paths)

    def on_drop_move_folder(self, event):
        paths = normalize_dnd_paths(self, event.data)
        if not paths:
            return
        dest = paths[0]
        if not dest.is_dir():
            messagebox.showerror("Not a folder", f"'{dest}' is not a folder.")
            return
        self._move_converted_to(dest)

    def move_choose_folder(self):
        chosen = filedialog.askdirectory(title="Choose Destination Folder")
        if chosen:
            self._move_converted_to(Path(chosen))

    def _move_converted_to(self, dest: Path):
        if not self.converted_paths:
            messagebox.showinfo(
                "Nothing to move", "Convert some files first, then try moving them."
            )
            return
        moved = 0
        for src in list(self.converted_paths):
            try:
                dest.mkdir(parents=True, exist_ok=True)
                target = dest / src.name
                if target.exists():
                    stem, ext = src.stem, src.suffix
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
            messagebox.showinfo(
                "Move complete", f"Moved {moved} file(s) to:\n{dest}"
            )

    # ------------- Conversion -------------
    def convert_now(self):
        if not self.queue:
            messagebox.showinfo("No files", "Add or drop some images first.")
            return
        out_dir = Path(self.output_dir_var.get())
        fmt = self.format_var.get().upper()
        quality = int(self.quality_var.get())
        keep_exif = bool(self.exif_var.get())
        suffix = self.suffix_var.get().strip() or "_converted"
        scale_label = self.upscale_var.get()
        scale = UPSCALE_OPTIONS.get(scale_label, 1.0)
        enhancements = self._collect_enhancement_settings()

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
                    out_path = export_image(
                        src,
                        out_dir,
                        fmt,
                        quality,
                        keep_exif,
                        suffix,
                        scale,
                        enhancements,
                    )
                    self.converted_paths.append(out_path)
                    successes += 1
                except Exception as e:
                    print(f"Failed: {src} -> {e}")
                    failures += 1
                finally:
                    self.progress.after(
                        0, lambda v=idx + 1: self.progress.config(value=v)
                    )

            def done():
                self.convert_btn.config(state="normal")
                self.status_var.set(
                    f"Done. Converted {successes} file(s), {failures} failed. Output: {out_dir}"
                )
                messagebox.showinfo(
                    "Conversion complete",
                    f"Converted {successes} file(s), {failures} failed.\n\n"
                    f"Output folder:\n{out_dir}\n\n"
                    f"Next: drop a folder onto the MOVE box to move them.",
                )

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
