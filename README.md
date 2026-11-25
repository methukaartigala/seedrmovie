# Seedr Movie Manager

A small Windows desktop app that searches movies on YTS, sends the chosen version to your
[Seedr](https://www.seedr.cc) cloud account, and downloads the finished file to your PC.

The window is a web page (`web/`) shown inside a native window with
[pywebview](https://pywebview.flowrl.com/); the logic lives in Python (`backend.py`).

## Features

- Search YTS (with a fallback source) and pick a quality and file size under 2 GB
- Seedr login with an optional **Remember credentials** (password is encrypted, see below)
- Resumable download to your PC: **Pause / Resume**, download speed, time remaining
- Automatic reconnect with growing waits if the connection drops; fresh link if Seedr's link expires
- **Download manager hand-off:** choose any download manager's program (IDM, Free Download Manager,
  aria2, others) and send the file to it instead, at the start or during a download
- Installer built with Inno Setup

## Run from source

Requires Python 3.10+ on Windows (the window uses Microsoft Edge WebView2, included in current Windows 10/11).

```
pip install -r requirements.txt
python main.py
```

## Build the installer

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php).
2. Double-click `build.bat`. It builds the app with PyInstaller and then the installer.
3. The installer is written to `installer_output\SeedrMovieManager_Setup.exe`.

Do not commit the installer. Attach it to a GitHub Release instead.

## Where your data is stored

Saved login and settings live in `%APPDATA%\SeedrMovieManager\` (outside the install folder, so
uninstalling the app does not remove them):

- `config.json`: email and the encrypted password
- `settings.json`: download folder and chosen download manager (no password)

On Windows the password is encrypted with Windows DPAPI, so only your Windows account can read it.
On other systems it uses the optional `cryptography` package; without it the password is not saved.
To remove a saved login, delete that folder or untick **Remember credentials** and click **Connect account**.

## Project layout

| File | Purpose |
| --- | --- |
| `main.py` | Starts the window |
| `backend.py` | Search, Seedr transfer, resumable downloader, download manager hand-off |
| `secure_store.py` | Password encryption |
| `web/` | The interface (HTML, CSS, JavaScript) |
| `build.bat`, `installer.iss` | Build scripts |

## Disclaimer

Only download content you have the right to download. You are responsible for how you use this tool.

## License

MIT, see [LICENSE](LICENSE).
