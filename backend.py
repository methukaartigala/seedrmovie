"""Backend for Seedr Movie Manager.

The window is a web page (see ./web). This module holds the logic the page calls:
YTS search, Seedr login/transfer, a resumable local downloader and the hand-off to an external download manager.
Methods WITHOUT a leading underscore are exposed to JavaScript (window.pywebview.api.*).
"""
import collections
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

import secure_store

APP_NAME = "SeedrMovieManager"
APP_TITLE = "Seedr Movie Manager"
APP_ID = "SeedrMovieManager.App.1"
APP_VERSION = "1.0.0"

MAX_SIZE_GB = 2.0
MAX_SIZE_BYTES = int(MAX_SIZE_GB * 1024 * 1024 * 1024)

MAX_RETRIES = 10          # automatic reconnect attempts before the download pauses itself
CHUNK_SIZE = 128 * 1024   # small enough that Pause reacts quickly

YTS_DOMAINS = [
    "https://yts.mx",
    "https://yts.lt",
    "https://yts.ag",
    "https://yts.am",
]

TRACKERS = [
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.leechers-paradise.org:6969",
]


def resource_path(name):
    """Path to a bundled resource (works from source and from PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _config_dir():
    """Per-user writable folder (installed apps can't write to Program Files)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    folder = Path(base) / APP_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(".")
    return folder


CONFIG_FILE = str(_config_dir() / "config.json")        # login (password is encrypted)
SETTINGS_FILE = str(_config_dir() / "settings.json")    # non-secret preferences


def _get(obj, key, default=None):
    """Read a field from a dict or an attribute-style object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_MANAGER_NAMES = {
    "idman": "IDM", "fdm": "Free Download Manager", "aria2c": "aria2", "jdownloader2": "JDownloader",
    "xdman": "XDM", "motrix": "Motrix", "eagleget": "EagleGet", "bitcomet": "BitComet",
    "persepolis": "Persepolis", "ndm": "Neat Download Manager", "gopeed": "Gopeed",
}


def manager_label(path):
    """Friendly name for a download-manager program, taken from its file name."""
    stem = Path(path).stem
    return _MANAGER_NAMES.get(stem.lower(), stem)


def manager_command(path, url, folder, name):
    """Return (command, copy_link). Known managers get their own options; any other program
    is simply started with the link, and the link is also copied to the clipboard as a fallback."""
    exe = Path(path).stem.lower()
    if exe == "idman":
        return [path, "/d", url, "/p", str(folder), "/f", name, "/n"], False
    if exe == "aria2c":
        return [path, "-d", str(folder), "-o", name, url], False
    if exe == "fdm":
        return [path, url], False
    return [path, url], True


class _Cancelled(Exception):
    pass


class _HandOff(Exception):
    """Raised inside the downloader when the user switches to an external download manager."""


class _Control:
    """Shared flags between the UI thread (pause/cancel/manager) and the download worker."""

    def __init__(self):
        self.run = threading.Event()
        self.run.set()
        self.cancelled = False
        self.handoff = False
        self.reason = "user"

    @property
    def paused(self):
        return not self.run.is_set()

    @property
    def interrupted(self):
        return self.cancelled or self.handoff or self.paused

    def pause(self, reason="user"):
        self.reason = reason
        self.run.clear()

    def resume(self):
        self.run.set()

    def cancel(self):
        self.cancelled = True
        self.run.set()

    def to_manager(self):
        self.handoff = True
        self.run.set()


class Api:
    def __init__(self):
        self._window = None
        self._candidates = []
        self._selected = None
        self._busy = False
        self._guard = threading.Lock()
        self._ctl = None
        self._stage_name = ""
        self._handoff_pending = False   # a movie is waiting in Seedr for the download manager
        self._creds = None              # (email, password) of the last transfer, in memory only

    # ------------------------------------------------------------------ plumbing
    def attach(self, window):
        self._window = window

    def _emit(self, type_, **data):
        window = self._window
        if window is None:
            return
        payload = json.dumps({"type": type_, **data}, ensure_ascii=False)
        try:
            window.evaluate_js(f"window.__emit && window.__emit({payload})")
        except Exception:
            pass

    def _log(self, text, level="info"):
        self._emit("log", text=str(text), level=level)

    def _status(self, title, detail=""):
        self._emit("status", title=title, detail=detail)

    def _set_stage(self, name):
        self._stage_name = name
        self._emit("stage", stage=name)

    # ------------------------------------------------------------------ saved login
    def _load_config(self):
        """Return (email, password, remember, notice)."""
        if not os.path.exists(CONFIG_FILE):
            return "", "", False, ""
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return "", "", False, f"Failed to load saved login: {e}"

        email = data.get("email", "")
        password, notice, legacy = "", "", False
        if data.get("password_enc"):
            try:
                password = secure_store.unprotect(data["password_enc"])
            except secure_store.SecureStoreError as e:
                notice = f"Saved password could not be decrypted: {e}"
        elif data.get("password"):
            password, legacy = data["password"], True  # old plaintext file
        remember = bool(email or password)
        if legacy and password:
            self._save_config(email, password, True)
        return email, password, remember, notice

    def _save_config(self, email, password, remember):
        if not remember:
            if os.path.exists(CONFIG_FILE):
                try:
                    os.remove(CONFIG_FILE)
                    self._log("Saved login removed.")
                except OSError:
                    pass
            return
        data = {"email": email}
        encrypted = False
        try:
            data["password_enc"] = secure_store.protect(password)
            encrypted = True
        except secure_store.SecureStoreError as e:
            self._log(f"Password NOT saved: {e}", "warn")  # never fall back to plaintext
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            try:
                os.chmod(CONFIG_FILE, 0o600)
            except OSError:
                pass
            self._log("Login saved (password encrypted)." if encrypted else "Email saved (password not saved).")
        except Exception as e:
            self._log(f"Failed to save login: {e}", "error")

    # ------------------------------------------------------------------ preferences
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self, **updates):
        data = self._load_settings()
        data.update(updates)
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError:
            pass

    # ------------------------------------------------------------------ external download manager
    def _manager_path(self):
        path = self._load_settings().get("manager_path")
        return path if path and os.path.isfile(path) else None

    @staticmethod
    def _copy_to_clipboard(text):
        try:
            if sys.platform == "win32":
                subprocess.run(["clip"], input=text.encode("utf-8"), check=True, creationflags=0x08000000)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            else:
                return False
            return True
        except Exception:
            return False

    def _launch_manager(self, url, folder, name):
        """Start the chosen download manager with the link. Returns True if the link was also copied."""
        path = self._manager_path()
        if not path:
            raise RuntimeError("The selected download manager could not be found. Please choose it again.")
        os.makedirs(folder, exist_ok=True)
        command, copy = manager_command(path, url, folder, name)
        copied = self._copy_to_clipboard(url) if copy else False
        subprocess.Popen(command, close_fds=True, cwd=os.path.dirname(path) or None)
        return copied

    def get_manager(self):
        path = self._manager_path()
        return {"path": path or "", "name": manager_label(path) if path else ""}

    def choose_manager(self):
        """Open a file picker so the user can select any download manager's program."""
        window = self._window
        if window is None:
            return None
        try:
            import webview
            dialog = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
            if dialog is None:
                dialog = webview.OPEN_DIALOG
            types = ("Programs (*.exe)", "All files (*.*)") if sys.platform == "win32" else ("All files (*.*)",)
            start = os.environ.get("ProgramFiles", str(Path.home())) if sys.platform == "win32" else str(Path.home())
            result = window.create_file_dialog(dialog, directory=start, allow_multiple=False, file_types=types)
        except Exception as e:
            self._log(f"File dialog error: {e}", "error")
            return None
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not os.path.isfile(path):
            self._emit("toast", kind="error", text="That file could not be found.")
            return None
        self._save_settings(manager_path=path)
        self._log(f"Download manager set to {manager_label(path)} ({path}).")
        return {"path": path, "name": manager_label(path)}

    def set_use_manager(self, enabled):
        enabled = bool(enabled)
        if enabled and not self._manager_path():
            return {"ok": False, "need_path": True}
        self._save_settings(use_manager=enabled)
        return {"ok": True}

    # ------------------------------------------------------------------ exposed: simple calls
    def get_state(self):
        email, password, remember, notice = self._load_config()
        settings = self._load_settings()
        folder = settings.get("folder")
        if not folder or not os.path.isdir(folder):
            folder = str(Path.home() / "Downloads")
        return {
            "email": email,
            "password": password,
            "remember": remember,
            "folder": folder,
            "use_manager": bool(settings.get("use_manager")) and bool(self._manager_path()),
            "manager_path": self._manager_path() or "",
            "manager_name": manager_label(self._manager_path()) if self._manager_path() else "",
            "version": APP_VERSION,
            "notice": notice,
        }

    def open_seedr(self):
        webbrowser.open("https://www.seedr.cc")
        return True

    def browse_folder(self, current=""):
        window = self._window
        if window is None:
            return None
        try:
            import webview
            dialog = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if dialog is None:
                dialog = webview.FOLDER_DIALOG
            start = current if current and os.path.isdir(current) else str(Path.home())
            result = window.create_file_dialog(dialog, directory=start)
        except Exception as e:
            self._log(f"Folder dialog error: {e}", "error")
            return None
        if result:
            folder = result[0] if isinstance(result, (list, tuple)) else result
            self._save_settings(folder=folder)
            return folder
        return None

    def select_variant(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return False
        if 0 <= index < len(self._candidates):
            self._selected = self._candidates[index]
            return True
        return False

    # ------------------------------------------------------------------ connect
    def connect(self, email, password, remember):
        threading.Thread(
            target=self._worker_connect, args=(str(email).strip(), str(password), bool(remember)), daemon=True
        ).start()
        return {"ok": True}

    def _worker_connect(self, email, password, remember):
        try:
            from seedrcc import Seedr
            Seedr.from_password(email, password)
        except Exception as e:
            self._log(f"Login failed: {e}", "error")
            self._emit("account", state="failed", message=f"Could not sign in: {e}")
            return
        self._log("Connected to Seedr successfully.")
        self._emit("account", state="connected")
        self._save_config(email, password, remember)

    # ------------------------------------------------------------------ search
    def search(self, query):
        query = str(query).strip()
        if not query:
            return {"ok": False}
        self._log(f"Searching for '{query}'...")
        self._status("Searching YTS...", f"Query: {query}")
        threading.Thread(target=self._worker_search, args=(query,), daemon=True).start()
        return {"ok": True}

    def _worker_search(self, query):
        try:
            candidates = self._search_yts(query)
        except Exception as e:
            self._log(f"Search error: {e}", "error")
            candidates = []
        candidates.sort(key=lambda c: c["seeds"], reverse=True)
        self._candidates = candidates
        self._selected = None
        items = [
            {"title": c["title"], "size_gb": round(c["size_bytes"] / (1024 ** 3), 2), "seeds": c["seeds"]}
            for c in candidates
        ]
        if items:
            self._log(f"Found {len(items)} variants under {MAX_SIZE_GB} GB.")
        else:
            self._log("No movie variants found under the size limit.", "warn")
        self._status("Search complete", f"{len(items)} version(s) found" if items else "No matches")
        self._emit("search_done", items=items)

    def _search_yts(self, movie_name):
        headers = {"User-Agent": "Mozilla/5.0"}
        for domain in YTS_DOMAINS:
            url = f"{domain}/api/v2/list_movies.json"
            params = {"query_term": movie_name, "limit": 10}
            try:
                self._log(f"Connecting to {domain}...")
                response = requests.get(url, params=params, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "ok" and data.get("data", {}).get("movie_count", 0) > 0:
                        return self._parse_yts_results(data["data"]["movies"])
            except requests.exceptions.RequestException:
                continue
        self._log("YTS mirrors blocked or unavailable. Falling back to Pirate Bay...", "warn")
        return self._search_apibay(movie_name)

    @staticmethod
    def _tracker_params():
        return "".join(f"&tr={urllib.parse.quote(tr)}" for tr in TRACKERS)

    def _parse_yts_results(self, movies):
        valid = []
        for movie in movies:
            title = movie.get("title_long", movie.get("title"))
            for torrent in movie.get("torrents", []):
                size_bytes = torrent.get("size_bytes", 0)
                if 0 < size_bytes <= MAX_SIZE_BYTES:
                    quality = torrent.get("quality")
                    magnet = (
                        f"magnet:?xt=urn:btih:{torrent.get('hash')}"
                        f"&dn={urllib.parse.quote(title)}+[{quality}]{self._tracker_params()}"
                    )
                    valid.append({
                        "title": f"{title} ({quality})",
                        "size_bytes": size_bytes,
                        "seeds": torrent.get("seeds", 0),
                        "magnet": magnet,
                    })
        return valid

    def _search_apibay(self, query):
        url = f"https://apibay.org/q.php?q={urllib.parse.quote(query)}"
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if response.status_code != 200:
                return []
            data = response.json()
            if not data or (len(data) == 1 and data[0].get("id") == "0"):
                return []
            results = []
            for item in data:
                size_bytes = int(item.get("size", 0))
                if 0 < size_bytes <= MAX_SIZE_BYTES:
                    name = item.get("name")
                    magnet = (
                        f"magnet:?xt=urn:btih:{item.get('info_hash')}"
                        f"&dn={urllib.parse.quote(name)}{self._tracker_params()}"
                    )
                    results.append({
                        "title": name,
                        "size_bytes": size_bytes,
                        "seeds": int(item.get("seeders", 0)),
                        "magnet": magnet,
                    })
            return results
        except Exception as e:
            self._log(f"Fallback search error: {e}", "error")
            return []

    # ------------------------------------------------------------------ transfer controls
    def pause(self):
        ctl = self._ctl
        if not ctl or self._stage_name != "download":
            return {"ok": False, "error": "Pause is available while the movie is downloading to your PC."}
        ctl.pause("user")
        return {"ok": True}

    def resume(self):
        ctl = self._ctl
        if not ctl:
            return {"ok": False}
        ctl.resume()
        return {"ok": True}

    def cancel(self):
        ctl = self._ctl
        if not ctl:
            return {"ok": False}
        ctl.cancel()
        return {"ok": True}

    def open_in_manager(self):
        ctl = self._ctl
        if not ctl or self._stage_name != "download":
            return {"ok": False, "error": "The download link is not ready yet. Try again once the PC download starts."}
        if not self._manager_path():
            return {"ok": False, "need_path": True}
        ctl.to_manager()
        return {"ok": True}

    def clear_seedr_storage(self, email="", password=""):
        creds = (str(email).strip(), str(password)) if email and password else self._creds
        if not creds:
            return {"ok": False, "error": "Enter your Seedr email and password first."}
        threading.Thread(target=self._worker_clear, args=creds, daemon=True).start()
        return {"ok": True}

    def _worker_clear(self, email, password):
        try:
            from seedrcc import Seedr
            seedr = Seedr.from_password(email, password)
            self._clear_seedr_storage(seedr)
            self._handoff_pending = False
            self._emit("handoff_cleared")
        except Exception as e:
            self._log(f"Could not clear Seedr storage: {e}", "error")
            self._emit("toast", kind="error", text=f"Could not clear Seedr storage: {e}")

    # ------------------------------------------------------------------ transfer
    def start_process(self, email, password, remember, folder, use_manager=False):
        email, password, folder = str(email).strip(), str(password), str(folder).strip()
        if not email or not password:
            return {"ok": False, "error": "Please enter your Seedr email and password."}
        if not self._selected:
            return {"ok": False, "error": "Please select a movie version first."}
        if not folder:
            return {"ok": False, "error": "Please choose a download folder."}
        if self._handoff_pending:
            return {"ok": False, "error": "Your last movie is still stored in Seedr for your download manager. "
                                          "Once it has finished, click “Clear Seedr storage” before starting a new one."}
        with self._guard:
            if self._busy:
                return {"ok": False, "error": "A transfer is already running."}
            self._busy = True
        self._save_config(email, password, bool(remember))
        self._save_settings(folder=folder, use_manager=bool(use_manager))
        self._emit("busy", busy=True)
        threading.Thread(
            target=self._worker_process,
            args=(email, password, self._selected, folder, bool(use_manager)),
            daemon=True,
        ).start()
        return {"ok": True}

    def _worker_process(self, email, password, candidate, folder, use_manager):
        ctl = _Control()
        self._ctl = ctl
        success = handoff = cancelled = False
        local_path = error = manager_name = ""
        try:
            from seedrcc import Seedr
            self._set_stage("sync")
            self._status("Connecting to Seedr...", "Authenticating")
            seedr = Seedr.from_password(email, password)
            self._log("Connected to Seedr successfully.")

            self._status("Cleaning Seedr...", "Clearing old storage files")
            self._clear_seedr_storage(seedr)

            self._status("Adding torrent...", candidate["title"])
            res = seedr.add_torrent(candidate["magnet"])
            torrent_id = _get(res, "user_torrent_id")
            self._log(f"Torrent added to Seedr. ID: {torrent_id}")

            self._status("Downloading in Seedr cloud...", "Polling progress")
            while True:
                if ctl.cancelled:
                    raise _Cancelled()
                contents = seedr.list_contents()
                active = False
                for t in _get(contents, "torrents", []) or []:
                    if str(_get(t, "id")) == str(torrent_id):
                        active = True
                        progress = float(_get(t, "progress", 0) or 0)
                        self._emit("progress", kind="sync", pct=progress,
                                   detail=f"Seedr cloud progress: {progress:g}%")
                        break
                if not active:
                    break
                for _ in range(10):  # 5 s, but react to Cancel quickly
                    if ctl.cancelled:
                        raise _Cancelled()
                    time.sleep(0.5)
            self._emit("progress", kind="sync", pct=100)
            self._log("Seedr cloud download complete.")

            self._status("Preparing download link...", "Checking Seedr storage")
            file_name, download_url = self._get_seedr_download_info(seedr)
            if not download_url:
                raise RuntimeError("Could not retrieve direct download link from Seedr.")
            self._log(f"File ready: {file_name}")

            safe_name = os.path.basename(file_name) or "movie.mp4"
            destination = Path(folder) / safe_name
            local_path = str(destination)

            if use_manager and not self._manager_path():
                self._emit("toast", kind="warn",
                           text="Your download manager could not be found, so the built-in downloader is being used instead.")
                use_manager = False

            if not use_manager:
                try:
                    self._set_stage("download")
                    refresh = lambda: self._get_seedr_download_info(seedr)[1]
                    self._download_locally(download_url, refresh, destination, ctl)
                    self._clear_seedr_storage(seedr)
                    success = True
                    self._status("Completed", local_path)
                except _HandOff:
                    use_manager = True
                    self._remove_part(destination)

            if use_manager:
                label = manager_label(self._manager_path())
                copied = self._launch_manager(download_url, folder, safe_name)
                handoff = success = True
                manager_name = label
                self._handoff_pending = True
                self._creds = (email, password)
                self._log(f"Sent to {label}. The movie stays in Seedr until you clear it.")
                if copied:
                    self._log(f"The download link was also copied to your clipboard. If {label} did not start the "
                              "download by itself, paste the link into it.", "warn")
                self._status(f"Handed to {label}", f"{label} is downloading the file")
                self._set_stage("handoff")
        except _Cancelled:
            cancelled = True
            self._log("Transfer cancelled. The torrent may still sit in your Seedr account; "
                      "it is cleared the next time you start.", "warn")
            self._status("Cancelled", "")
            if local_path:
                self._remove_part(Path(local_path))
        except Exception as e:
            error = str(e)
            self._log(f"Process error: {e}", "error")
            self._status("Failed", error)
        finally:
            self._ctl = None
            self._stage_name = ""
            self._busy = False
            self._emit("busy", busy=False)
            self._emit("process_done", success=success, handoff=handoff, cancelled=cancelled,
                       path=local_path, error=error, manager=manager_name)

    def _clear_seedr_storage(self, seedr):
        try:
            contents = seedr.list_contents()
            for folder in _get(contents, "folders", []) or []:
                folder_id = _get(folder, "id")
                if folder_id:
                    seedr.delete_folder(folder_id)
            for file in _get(contents, "files", []) or []:
                file_id = _get(file, "id")
                if file_id:
                    seedr.delete_file(file_id)
            self._log("Seedr storage cleared.")
        except Exception as e:
            self._log(f"Storage clearance warning: {e}", "warn")

    def _get_seedr_download_info(self, seedr):
        contents = seedr.list_contents()
        folders = _get(contents, "folders", []) or []
        if not folders:
            return None, None
        folder_id = _get(folders[-1], "id")
        folder_contents = seedr.list_contents(folder_id=folder_id)
        files = _get(folder_contents, "files", []) or []
        if not files:
            return None, None
        video = max(files, key=lambda f: _get(f, "size", 0) or 0)
        file_id = _get(video, "folder_file_id", _get(video, "id"))
        file_name = _get(video, "name", "movie.mp4")
        info = seedr.fetch_file(file_id)
        return file_name, _get(info, "url")

    # ------------------------------------------------------------------ resumable downloader
    @staticmethod
    def _part_path(destination):
        return destination.with_name(destination.name + ".part")

    def _remove_part(self, destination):
        try:
            self._part_path(destination).unlink()
        except OSError:
            pass

    @staticmethod
    def _wait(ctl, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if ctl.interrupted:
                return
            time.sleep(0.25)

    def _download_locally(self, url, refresh_url, destination, ctl):
        """Download to <name>.part, resuming with HTTP Range after a pause or a dropped connection."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = self._part_path(destination)
        if part.exists():
            part.unlink()  # a leftover from an older run can't be verified, so start clean
        self._status("Downloading to PC...", str(destination))
        self._log(f"Saving to local disk: {destination}")

        pos, total = 0, 0
        attempts = 0
        samples = collections.deque()
        last_emit = 0.0
        started = time.monotonic()
        self._emit("progress", kind="download", pct=0, done=0, total=0, speed=0, eta=None)

        while True:
            if ctl.cancelled:
                raise _Cancelled()
            if ctl.handoff:
                raise _HandOff()
            if ctl.paused:
                reason = ctl.reason
                self._emit("paused", paused=True, reason=reason)
                self._status(
                    "Paused" if reason == "user" else "Connection lost",
                    "Your progress is saved. Click Resume to continue." if reason == "user"
                    else "Progress is saved. Click Resume when you're back online.",
                )
                self._emit("progress", kind="download", pct=round(pos / total * 100, 1) if total else 0,
                           done=pos, total=total, speed=0, eta=None)
                ctl.run.wait()
                samples.clear()
                if not (ctl.cancelled or ctl.handoff):
                    self._emit("paused", paused=False, reason="")
                    self._status("Resuming...", "")
                continue

            headers = {"Range": f"bytes={pos}-"} if pos else {}
            try:
                with requests.get(url, stream=True, headers=headers, timeout=(10, 30)) as r:
                    if r.status_code == 416 and total and pos >= total:
                        break  # nothing left to fetch
                    r.raise_for_status()

                    if pos and r.status_code != 206:
                        pos = 0  # server ignored Range: start over
                    cr = r.headers.get("Content-Range", "")
                    clen = int(r.headers.get("content-length", 0) or 0)
                    if r.status_code == 206 and "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                        total = int(cr.rsplit("/", 1)[1])
                    elif r.status_code == 206:
                        total = pos + clen
                    else:
                        total = clen

                    with open(part, "ab" if pos else "wb") as fh:
                        for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                            if ctl.interrupted:
                                break
                            if not chunk:
                                continue
                            fh.write(chunk)
                            pos += len(chunk)
                            attempts = 0  # made progress, so the connection is healthy

                            now = time.monotonic()
                            samples.append((now, pos))
                            while samples and now - samples[0][0] > 6:
                                samples.popleft()
                            if now - last_emit >= 0.5:
                                last_emit = now
                                speed = 0.0
                                if len(samples) > 1:
                                    dt = samples[-1][0] - samples[0][0]
                                    if dt > 0.3:
                                        speed = (samples[-1][1] - samples[0][1]) / dt
                                eta = (total - pos) / speed if total and speed > 1 else None
                                pct = pos / total * 100 if total else 0
                                self._emit("progress", kind="download", pct=round(pct, 1), done=pos,
                                           total=total, speed=speed, eta=eta)

                if ctl.interrupted:
                    continue  # top of the loop handles pause / cancel / hand-off
                if total and pos < total:
                    raise requests.exceptions.ConnectionError("The connection closed before the file finished.")
                break

            except requests.exceptions.RequestException as e:
                if ctl.interrupted:
                    continue
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (401, 403, 404, 410) and refresh_url:
                    try:
                        fresh = refresh_url()
                        if fresh:
                            url = fresh
                            self._log("Download link expired; fetched a fresh one from Seedr.", "warn")
                    except Exception as refresh_error:
                        self._log(f"Could not refresh the download link: {refresh_error}", "warn")
                attempts += 1
                if attempts > MAX_RETRIES:
                    self._log("Still can't reach the server. Your progress is saved; click Resume to try again.",
                              "warn")
                    attempts = 0
                    ctl.pause("connection")
                    continue
                delay = min(5 * 2 ** (attempts - 1), 60)
                self._log(f"Connection problem ({type(e).__name__}). Retrying in {delay}s "
                          f"(attempt {attempts}/{MAX_RETRIES}).", "warn")
                self._status("Connection lost", f"Retrying in {delay}s (attempt {attempts}/{MAX_RETRIES})")
                self._emit("progress", kind="download", pct=round(pos / total * 100, 1) if total else 0,
                           done=pos, total=total, speed=0, eta=None)
                self._wait(ctl, delay)

        if total and pos < total:
            raise RuntimeError("The download ended early. Please try again.")
        os.replace(part, destination)
        elapsed = max(time.monotonic() - started, 0.001)
        self._emit("progress", kind="download", pct=100, done=pos, total=total or pos, speed=0, eta=0)
        self._log(f"Local download completed ({round(pos / 1048576, 1)} MB, "
                  f"avg {round(pos / 1048576 / elapsed, 2)} MB/s).")
