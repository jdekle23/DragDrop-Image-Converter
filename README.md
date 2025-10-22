# 🖼️ DragDrop Image Converter

A lightweight, cross-platform Python app to **convert multiple image formats** (WebP, PNG, JPG, JPEG, etc.) using an easy **drag-and-drop interface**.  
Built with **Tkinter**, **Pillow**, and **tkinterdnd2** for simple desktop use — no browser, no ads, no nonsense.

---

## ✨ Features

✅ Drag-and-drop multiple files or folders  
✅ Supports JPG, JPEG, PNG, WEBP, TIFF, BMP  
✅ Converts transparent images safely for JPEG  
✅ Preserves EXIF metadata (if available)  
✅ Move converted files with one drop  
✅ Works on **Windows**, **macOS**, and **Linux**

---

## 🧩 Installation

1. **Clone the repo**
   ```bash
   git clone https://github.com/<your-username>/dragdrop-image-converter.git
   cd dragdrop-image-converter
    ```

## 🚀 Merging the latest UI features into `main`

If you sync with GitHub and see merge markers like `<<<<<<<`, `=======`, or `>>>>>>>` inside
`Drag Drop Image Converter.py`, keep the new upscale/enhancement UI by choosing **Accept Incoming
Change** (or manually deleting the markers and leaving the new block). Once the file looks clean:

```bash
python -m compileall "Drag Drop Image Converter.py"  # quick syntax check
git add "Drag Drop Image Converter.py"
git commit -m "Resolve merge after enhancement update"
git push
```

That sequence resolves the conflict and lets you fast-forward `main` with the enhanced exporter.
