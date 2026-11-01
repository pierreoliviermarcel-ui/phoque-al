# Third-party components

Phoque Al itself is licensed under the GNU GPL v3.0 or later. It uses the components below, which keep their own
licenses. The Windows installer bundles only the components marked **bundled**; the others are installed from
PyPI when building, or downloaded at the user's request.

## Bundled in the Windows installer

| Component | License | Source |
|---|---|---|
| Qt for Python (PySide6) and Qt 6 | LGPL-3.0 | https://code.qt.io/ · https://download.qt.io/ |
| libVLC and VLC plugins | LGPL-2.1-or-later (some plugins GPL-2.0-or-later) | https://www.videolan.org/vlc/download-sources.html |
| python-vlc | LGPL-2.1-or-later | https://github.com/oaubert/python-vlc |
| FFmpeg / FFprobe (Windows build) | GPL-3.0 (the builds used are GPL builds) | https://ffmpeg.org/download.html · build: https://www.gyan.dev/ffmpeg/builds/ |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | BSD-3-Clause | https://numpy.org |
| Pillow | MIT-CMU (HPND) | https://python-pillow.org |
| rarfile | ISC | https://github.com/markokr/rarfile |
| qrcode | BSD | https://github.com/lincolnloop/python-qrcode |
| PyInstaller bootloader | GPL-2.0 with bootloader exception | https://pyinstaller.org |

The license texts of FFmpeg and VLC are copied into the `licenses` folder of the installation by `build_windows.bat`.

**LGPL notice (Qt, VLC):** these libraries are shipped as separate, unmodified DLLs in the installation folder.
You may replace them with your own compatible builds. Their complete source code is available at the addresses
above; on request, the maintainers of Phoque Al will also provide the exact sources of the versions shipped.

**GPL notice (FFmpeg):** FFmpeg is run as a separate program. Its source code is available at ffmpeg.org; the
exact build is identified in `licenses/FFmpeg_README.txt`.

## Not bundled — used if installed by the user

| Component | License | Notes |
|---|---|---|
| 7-Zip | LGPL-2.1 + unRAR restriction | used to read RAR photo archives if installed |
| UnRAR (RARLAB) | freeware UnRAR license | used to read RAR photo archives if installed |

## Downloaded at the user's request (optional AI features)

| Component | License | Downloaded from |
|---|---|---|
| CLIP ViT-B/32 (quantized ONNX export) — AI tag suggestions | MIT (OpenAI CLIP); ONNX export by Xenova | Hugging Face (`Xenova/clip-vit-base-patch32`) |
| MDX-Net "Kim Vocal 2" — music / voice separation | model by KimberleyJSN, distributed by the Ultimate Vocal Remover project (UVR code: MIT); no separate model license is stated — not redistributed here | https://github.com/TRvlvr/model_repo |
| Real-ESRGAN ncnn-vulkan + models `realesrgan-x4plus`, `realesr-animevideov3` — AI upscaling | BSD-3-Clause (Real-ESRGAN, ncnn) | https://github.com/xinntao/Real-ESRGAN |
| `upscayl-lite-4x` model — fast AI upscaling | AGPL-3.0 (Upscayl) | https://github.com/upscayl/upscayl |

These files are stored in the `data/models` folder of the installation and are not part of this repository.

## Tools used for building only

Inno Setup (https://jrsoftware.org/isinfo.php, Inno Setup License) and ReportLab (BSD) for the PDF user guide.
