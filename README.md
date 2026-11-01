# Phoque Al

**Your local video library, streaming-style — on as many screens as you have.**

Phoque Al is a free, open-source Windows application that turns the folders of videos on your drives into a
streaming-service-like library: a home page with a featured banner, rows by theme, resume where you left off,
favourites and recommendations. It is built for people who watch on several screens at once, who like their
videos cut to music, and who want everything to stay private on their own computer.

No account, no cloud, no telemetry: your library, your history and your settings never leave your PC.

> The interface is available in **English, French, Spanish and German** (⋯ → *Langue / Language*).

---

## Features

### Library
- **Streaming-style home page**: animated featured banner with live video previews, then rows in a sensible
  order — *Resume*, *Favourites*, *Recommended for you*, *Playlists*, *Evenings*, *Moments*, *Top rated*,
  *People & tags*, *Recently added*, *Photo albums* and one row per theme (each sub-folder is a theme).
- **Hover previews** (video or animated thumbnails), automatic thumbnails, durations, play counts.
- **External drives done right**: drives are recognised by their serial number, so the library survives a change
  of drive letter, and videos simply show as unavailable while a drive is unplugged.
- **People & tags from file names**: the app proposes names and tags found in your file names; whatever you
  confirm is then recognised everywhere (even when words are glued together), with aliases and merging.
- **AI tag suggestions** (optional, fully local): a CLIP model looks at a few frames of each video and proposes
  tags from a list you can edit. It learns from what you accept or reject. Nothing is applied without your
  consent unless you allow it above a confidence threshold.
- **Ratings (1–5 stars), likes, playlists, favourite themes**, search by title, studio, theme or person.
- **Photo albums**: image folders and ZIP / RAR / CBZ / CBR archives, optionally mixed into themes and montages.
- **Duplicate finder**: exact duplicates and *visual* duplicates (same video in different qualities); the best
  copy is kept, the others go to a recycle folder — nothing is ever deleted without confirmation.
- **Statistics**: what you watch, when, your most watched themes and people.

### Playback on many screens
- **Up to 9 screens at once**, each with its own player, plus a screen picker on a **mini-map** of your real
  screen layout when there are 4 screens or more.
- **Mosaics**: 2 to 16 videos on one screen, each with its own sound, volume and controls; save and reload them.
- **Video wall**: one video spread across several screens, with bezel compensation and a test pattern to
  align them.
- **Screen groups**, *shift videos to the next screen*, *play on all screens*, per-screen or global controls.
- **Evenings**: save the whole setup (what plays where, mosaics, music, montage) and relaunch it in one click.
- Fill / crop / aspect-ratio modes per screen (16:9, 21:9, 4:3, 9:16…), **blurred background** for videos that
  don't fill the screen, transitions between videos, loop / shuffle / next-in-theme.
- **Scene detection** (jump to next scene), **favourite moments** (mark an instant, replay all your moments as
  a reel), **highlights** (the most intense passages, found from motion and sound).

### Music
- **Background music player** for a music folder, with its own volume.
- **Beat montage**: the videos on screen change on the beats of the music, faster when the music builds up.
- **Neon beat bar**: a cyberpunk-style animated bar under music videos that follows the detected beat
  (several styles, adjustable offset).
- **Music-video generator**: pick a track and some videos; the app cuts them on the beat with 8 styles
  (classic, stutter, split screen, 2×2 grid, mirror, pulse, speed ramps, mix). The result has three audio
  tracks: *mix*, *music only* and *video sounds only*.
- **Automatic ducking**: while your own music plays, music videos switch to their "video sounds only" track (or
  are lowered/muted), so two songs never play over each other.
- **AI music removal** (optional, local): an MDX-Net model separates the music from voices and other sounds of
  an existing video, so the ducking can keep the voices.
- **Sessions**: timed programmes with phases (calm, build-up, intense, slow-down, pauses…) that drive the montage
  speed and volume, with an on-screen timer.

### Picture
- **AI upscaling** (optional): Real-ESRGAN, running on your graphics card (NVIDIA, AMD or Intel via Vulkan),
  enlarges and cleans low-quality videos (blur, noise, compression blocks). A 30-second test comes first; the
  result is a new "[HD]" file next to the original, which is never modified. Processing resumes after a restart.

### Control
- **Phone / tablet remote** in the browser (same Wi-Fi, QR code + PIN, no app to install): screens, library,
  playlists, music, sessions — with a one-hand portrait mode and a tablet grid layout.
- **Satellite PCs**: another computer running Phoque Al on the network adds its screens and its library.
- **Game controller** (Xbox / XInput) and fully **remappable keyboard shortcuts**.
- **Discreet mode** (no thumbnails, neutral titles) and a **panic key** (F12: everything paused and hidden).
- **Start with Windows** (optional), minimised and in discreet mode.

---

## Installation

1. Download `PhoqueAl_Setup.exe` from the [Releases](../../releases) page.
2. Run it — no administrator rights are needed (per-user install). VLC and FFmpeg are included.
3. Open *Settings → Library → Add a source folder* and pick your video folders.

Optional AI features download their model the first time you use them (from GitHub or Hugging Face).

**Requirements**: Windows 10 or 11, 64-bit. A graphics card with Vulkan support is recommended for AI upscaling.

## Running from source

```bat
git clone https://github.com/pierreoliviermarcel-ui/phoque-al.git
cd phoque-al
lancer_phoqueal.bat
```

You need Python 3.11+ and VLC (64-bit) installed; FFmpeg must be on the `PATH` (e.g. `winget install Gyan.FFmpeg`).
To build the installer yourself: `build_windows.bat` (PyInstaller + Inno Setup).

### Project layout

| Path | Content |
|---|---|
| `phoqueal/` | the application (PySide6 UI, libVLC players, SQLite library) |
| `phoqueal/extras/` | optional features, each in its own module, installed at start-up without touching the core |
| `phoqueal/extras/i18n.py`, `i18n_data.py` | runtime translation (French source strings → EN / ES / DE) |
| `docs/notice_source.py` | generates the PDF user guide (French) |
| `installer/` | Inno Setup script |
| `tools/download_models.py` | pre-downloads the AI models (for offline machines) |

## Contributing

Contributions are welcome: bug reports, translations, new features, packaging for other systems.
Fork the repository, make your changes on a branch and open a pull request. The source strings are in French;
to add or fix a translation, edit `phoqueal/extras/i18n_data.py`.

## License

Phoque Al is free software released under the **GNU General Public License v3.0 or later** — see [LICENSE](LICENSE).
You may use, study, modify and redistribute it; modified versions you distribute must remain under the GPL.

Third-party components keep their own licenses — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Phoque Al is a media player and organiser for your own files; it does not provide, download or host any videos.
