#!/usr/bin/env python3
"""
YouTube Downloader - a small GTK 3 GUI around yt-dlp.

Features
  * Paste one or more URLs (one per line) and download them in a background thread
  * Choose video quality (best / 1080p / 720p / 480p / 360p) or audio only
  * Live progress bar, speed and ETA, plus a scrolling activity log
  * Pick the download folder (defaults to this script's folder)
  * Cancel the current batch at any time

Requires: python3-gi (GTK 3) and yt-dlp (python module or CLI).
Note: formats are chosen so that no ffmpeg merge is needed. If ffmpeg is
installed you can switch to "best (merged)" for the highest quality.
"""

import os
import shutil
import sys
import threading
import datetime

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk, Pango  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Prefer the bundled, up-to-date yt-dlp in ./_vendor (older system copies
# fail with "Requested format is not available" on current YouTube).
_VENDOR = os.path.join(APP_DIR, "_vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)
# Bundled ffmpeg (needed to merge video+audio and for MP3)
_VENDOR_BIN = os.path.join(_VENDOR, "bin")
if os.path.isdir(_VENDOR_BIN):
    os.environ["PATH"] = _VENDOR_BIN + os.pathsep + os.environ.get("PATH", "")

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

# Node.js lets yt-dlp solve YouTube's JS challenges (deno is the default)
_NODE = shutil.which("node") or shutil.which("nodejs")
JS_RUNTIMES = {"node": {"path": _NODE}} if _NODE else {}
DEFAULT_OUT = APP_DIR

QUALITY_CHOICES = [
    ("Best", "bestvideo+bestaudio/best"),
    ("1080p", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
    ("720p", "bestvideo[height<=720]+bestaudio/best[height<=720]"),
    ("480p", "bestvideo[height<=480]+bestaudio/best[height<=480]"),
    ("360p", "bestvideo[height<=360]+bestaudio/best[height<=360]"),
    ("Audio only (m4a)", "bestaudio[ext=m4a]/bestaudio"),
]

CSS = b"""
/* Modern flat dark theme */
window { background-color: #0e1117; color: #e6e8ee;
         font-family: 'Cantarell', 'Ubuntu', 'Segoe UI', sans-serif; font-size: 13px; }
.header { background-color: #161b26; border-radius: 16px; padding: 16px 20px; }
.title { color: #ffffff; font-size: 22px; font-weight: 700; }
.subtitle { color: #7d8599; font-size: 12px; }
.card { background-color: #161b26; border-radius: 16px; padding: 16px;
        border: 1px solid #222938; }
.section { color: #7d8599; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
.label { color: #c9cfdb; }

entry, textview, combobox button, filechooserbutton button, button {
    border-radius: 10px; }
combobox button, filechooserbutton button {
    background-color: #1d2331; color: #e6e8ee; border: 1px solid #2a3142; }

textview, scrolledwindow.log { background-color: #0b0e14; }
textview text { background-color: #0b0e14; color: #9be7b0; font-family: monospace;
                font-size: 12px; }
.urls textview text { color: #e6e8ee; }
.urls { border: 1px solid #2a3142; border-radius: 12px; }

check { min-width: 16px; min-height: 16px; border-radius: 5px; }
check:checked { background-color: #6c5ce7; color: #ffffff; }

button { background-image: none; background-color: #1d2331; color: #e6e8ee;
         border: 1px solid #2a3142; padding: 7px 16px; box-shadow: none; }
button:hover { background-color: #252d40; }
button:disabled { color: #4b5366; background-color: #161b26; }

button.download { background-image: none; background-color: #6c5ce7; color: #ffffff;
                  font-weight: 700; padding: 9px 26px; border: none; }
button.download:hover { background-color: #7d6ff0; }
button.download:disabled { background-color: #2a2f45; color: #6b7290; }
button.cancel { color: #ff7b7b; }
button.cancel:hover { background-color: #3a1f27; }

progressbar trough { background-color: #222938; border-radius: 10px; min-height: 10px; }
progressbar progress { background-image: none; background-color: #6c5ce7;
                       border-radius: 10px; }
progressbar text { color: #c9cfdb; font-size: 11px; }
.status { color: #ffffff; font-weight: 600; font-size: 14px; }
"""


def human_size(n):
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class Cancelled(Exception):
    pass


class DownloaderApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="YouTube Downloader")
        self.set_default_size(760, 680)
        self.set_border_width(16)
        self.connect("destroy", Gtk.main_quit)

        self.cancel_event = threading.Event()
        self.worker = None
        self.out_dir = DEFAULT_OUT
        os.makedirs(self.out_dir, exist_ok=True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)

        header = self._card()
        header.get_style_context().add_class("header")
        root.pack_start(header, False, False, 0)

        title = Gtk.Label(label="YouTube Downloader")
        title.get_style_context().add_class("title")
        title.set_xalign(0)
        header.pack_start(title, False, False, 0)

        sub = Gtk.Label(label="Paste links, choose a format, download. Powered by yt-dlp.")
        sub.get_style_context().add_class("subtitle")
        sub.set_xalign(0)
        header.pack_start(sub, False, False, 0)

        # --- Input card -------------------------------------------------
        card = self._card()
        root.pack_start(card, False, False, 0)

        url_label = Gtk.Label(label="VIDEO LINKS  (one per line)")
        url_label.get_style_context().add_class("section")
        url_label.set_xalign(0)
        card.pack_start(url_label, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(90)
        sw.get_style_context().add_class("urls")
        self.url_view = Gtk.TextView()
        self.url_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.url_view.set_left_margin(10)
        self.url_view.set_top_margin(8)
        sw.add(self.url_view)
        card.pack_start(sw, True, True, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.pack_start(row, False, False, 0)

        row.pack_start(self._label("Quality:"), False, False, 0)
        self.quality = Gtk.ComboBoxText()
        for name, _ in QUALITY_CHOICES:
            self.quality.append_text(name)
        self.quality.set_active(0)
        row.pack_start(self.quality, False, False, 0)

        self.mp3_check = Gtk.CheckButton(label="Audio only → MP3")
        self.mp3_check.connect("toggled", self.on_mp3_toggled)
        row.pack_start(self.mp3_check, False, False, 0)

        row.pack_start(self._label("Save to:"), False, False, 0)
        self.folder_btn = Gtk.FileChooserButton(
            title="Choose download folder",
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.folder_btn.set_filename(self.out_dir)
        self.folder_btn.set_hexpand(True)
        row.pack_start(self.folder_btn, True, True, 0)

        # --- Buttons ----------------------------------------------------
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        root.pack_start(btns, False, False, 0)

        self.dl_btn = Gtk.Button(label="Download")
        self.dl_btn.get_style_context().add_class("download")
        self.dl_btn.connect("clicked", self.on_download)
        btns.pack_start(self.dl_btn, False, False, 0)

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.get_style_context().add_class("cancel")
        self.cancel_btn.set_sensitive(False)
        self.cancel_btn.connect("clicked", self.on_cancel)
        btns.pack_start(self.cancel_btn, False, False, 0)

        clear_btn = Gtk.Button(label="Clear log")
        clear_btn.connect("clicked", lambda *_: self.log_buf.set_text(""))
        btns.pack_end(clear_btn, False, False, 0)

        open_btn = Gtk.Button(label="Open folder")
        open_btn.connect("clicked", self.on_open_folder)
        btns.pack_end(open_btn, False, False, 0)

        # --- Progress card ----------------------------------------------
        pcard = self._card()
        root.pack_start(pcard, False, False, 0)

        self.status = Gtk.Label(label="Ready")
        self.status.get_style_context().add_class("status")
        self.status.set_xalign(0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        pcard.pack_start(self.status, False, False, 0)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("idle")
        pcard.pack_start(self.progress, False, False, 0)

        # --- Log --------------------------------------------------------
        lsw = Gtk.ScrolledWindow()
        lsw.set_vexpand(True)
        lsw.get_style_context().add_class("log")
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.log_view.set_left_margin(8)
        self.log_buf = self.log_view.get_buffer()
        lsw.add(self.log_view)
        root.pack_start(lsw, True, True, 0)

        if yt_dlp is None:
            self.log("ERROR: yt-dlp is not installed. Run: pip install -U yt-dlp")
            self.dl_btn.set_sensitive(False)
        else:
            self.log(f"yt-dlp {yt_dlp.version.__version__} ready. "
                     f"Saving to: {self.out_dir}")

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def _card():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("card")
        return box

    @staticmethod
    def _label(text):
        lbl = Gtk.Label(label=text)
        lbl.get_style_context().add_class("label")
        return lbl

    def log(self, text):
        """Thread-safe log append."""
        def _append():
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            end = self.log_buf.get_end_iter()
            self.log_buf.insert(end, f"[{stamp}] {text}\n")
            mark = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
            self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
            return False
        GLib.idle_add(_append)

    def set_status(self, text, fraction=None, bar_text=None):
        def _set():
            self.status.set_text(text)
            if fraction is not None:
                self.progress.set_fraction(max(0.0, min(1.0, fraction)))
            if bar_text is not None:
                self.progress.set_text(bar_text)
            return False
        GLib.idle_add(_set)

    def set_busy(self, busy):
        def _set():
            self.dl_btn.set_sensitive(not busy and yt_dlp is not None)
            self.cancel_btn.set_sensitive(busy)
            return False
        GLib.idle_add(_set)

    def get_urls(self):
        buf = self.url_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return [u.strip() for u in text.splitlines() if u.strip()]

    # ---- actions -------------------------------------------------------
    def on_download(self, _btn):
        urls = self.get_urls()
        if not urls:
            self.log("Paste at least one URL first.")
            return
        out_dir = self.folder_btn.get_filename() or DEFAULT_OUT
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        mp3 = self.mp3_check.get_active()
        if mp3 and not shutil.which("ffmpeg"):
            self.log("MP3 needs ffmpeg, which is not installed. Run: sudo apt install ffmpeg")
            return
        fmt = "bestaudio/best" if mp3 else QUALITY_CHOICES[self.quality.get_active()][1]
        if not mp3 and not shutil.which("ffmpeg"):
            # Without ffmpeg, only single-file formats can be used
            fmt = fmt.split("/")[0].replace("bestvideo", "best").replace("+bestaudio", "")

        self.cancel_event.clear()
        self.set_busy(True)
        self.worker = threading.Thread(
            target=self._run_batch, args=(urls, out_dir, fmt, mp3), daemon=True)
        self.worker.start()

    def on_cancel(self, _btn):
        self.cancel_event.set()
        self.log("Cancelling after the current chunk...")

    def on_mp3_toggled(self, cb):
        # Quality choice does not apply to MP3 output
        self.quality.set_sensitive(not cb.get_active())

    def on_open_folder(self, _btn):
        import subprocess
        folder = self.folder_btn.get_filename() or DEFAULT_OUT
        subprocess.Popen(["xdg-open", folder])

    def _run_batch(self, urls, out_dir, fmt, mp3):
        ok, failed = 0, 0
        for i, url in enumerate(urls, 1):
            if self.cancel_event.is_set():
                break
            self.log(f"[{i}/{len(urls)}] {url}")
            try:
                self._download_one(url, out_dir, fmt, mp3, i, len(urls))
                ok += 1
            except Cancelled:
                self.log("Cancelled.")
                break
            except Exception as e:  # yt-dlp errors and others
                failed += 1
                self.log(f"  ✖ Failed: {e}")
        self.log(f"Finished: {ok} succeeded, {failed} failed.")
        self.set_status("Cancelled" if self.cancel_event.is_set() else "All done ✔",
                        fraction=1.0 if not self.cancel_event.is_set() else None,
                        bar_text="done" if not self.cancel_event.is_set() else "cancelled")
        self.set_busy(False)

    def _download_one(self, url, out_dir, fmt, mp3, idx, total):
        def hook(d):
            if self.cancel_event.is_set():
                raise Cancelled()
            if d["status"] == "downloading":
                done = d.get("downloaded_bytes") or 0
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                frac = (done / tot) if tot else 0.0
                speed = d.get("speed")
                eta = d.get("eta")
                spd = f"{human_size(speed)}/s" if speed else "-"
                eta_s = f"{eta}s" if eta is not None else "-"
                self.set_status(f"Downloading {idx}/{total}  •  {spd}  •  ETA {eta_s}",
                                fraction=frac,
                                bar_text=f"{frac * 100:.1f}%  ({human_size(done)} / {human_size(tot)})")
            elif d["status"] == "finished":
                self.set_status(f"Processing {idx}/{total}…", fraction=1.0,
                                bar_text="processing")
                self.log(f"  ↳ saved {os.path.basename(d.get('filename', ''))}")

        opts = {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(out_dir, "%(title).200B [%(id)s].%(ext)s"),
            "progress_hooks": [hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "restrictfilenames": False,
            "retries": 5,
            # YouTube needs a JS runtime for full format lists
            "js_runtimes": JS_RUNTIMES,
            "remote_components": ["ejs:github"],
        }
        if mp3:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            if self.cancel_event.is_set():
                raise Cancelled()
            raise


def main():
    if yt_dlp is None:
        print("yt-dlp is not installed. Install with: pip install -U yt-dlp")
    win = DownloaderApp()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
