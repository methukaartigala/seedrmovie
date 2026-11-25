"""Seedr Movie Manager — desktop launcher (pywebview window around ./web)."""
import os
import sys
import time

import webview

from backend import APP_ID, APP_TITLE, Api, resource_path


def _set_windows_icon(title):
    """Put icon.ico on the window title bar/taskbar (pywebview doesn't do this on Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        hwnd = None
        for _ in range(60):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return
        ico = resource_path("icon.ico")
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        for which, size in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
            handle = user32.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if handle:
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
    except Exception:
        pass


def _on_started(window):
    _set_windows_icon(APP_TITLE)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            # Own taskbar identity so Windows shows our icon instead of Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    api = Api()
    window = webview.create_window(
        APP_TITLE,
        resource_path(os.path.join("web", "index.html")),
        js_api=api,
        width=1280,
        height=900,
        min_size=(980, 700),
        background_color="#0d1117",
        text_select=True,
    )
    api.attach(window)
    webview.start(_on_started, window)


if __name__ == "__main__":
    main()
