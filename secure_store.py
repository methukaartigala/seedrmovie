"""Encrypt/decrypt saved credentials without extra dependencies where possible.

Windows : DPAPI (CryptProtectData) via ctypes. The data can only be decrypted
          by the same Windows user account on the same machine.
Other   : Fernet (AES) from the optional `cryptography` package, with a random
          key stored in a separate owner-only file.
If neither is available, protect() raises SecureStoreError and the caller
should NOT save the password.
"""
import base64
import os
import sys
from pathlib import Path

_ENTROPY = b"SeedrMovieManager-v1"
KEY_FILE = Path.home() / ".seedr_movie_manager.key"


class SecureStoreError(Exception):
    pass


# ---------- Windows DPAPI ----------
def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make_blob(raw):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    blob_in, _keep1 = make_blob(data)
    blob_ent, _keep2 = make_blob(_ENTROPY)
    blob_out = DATA_BLOB()

    func = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    args = (ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, 0,
            ctypes.byref(blob_out))
    if encrypt:
        args = (ctypes.byref(blob_in), "SeedrMovieManager", ctypes.byref(blob_ent),
                None, None, 0, ctypes.byref(blob_out))
    if not func(*args):
        raise SecureStoreError("Windows DPAPI operation failed.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


# ---------- Fernet fallback ----------
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise SecureStoreError(
            "No secure storage available. Install the 'cryptography' package "
            "(pip install cryptography) or run on Windows.")
    if KEY_FILE.exists():
        key = KEY_FILE.read_bytes()
    else:
        key = Fernet.generate_key()
        fd = os.open(str(KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    return Fernet(key)


# ---------- Public API ----------
def protect(text: str) -> str:
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    return "fernet:" + _fernet().encrypt(raw).decode("ascii")


def unprotect(token: str) -> str:
    try:
        scheme, _, payload = token.partition(":")
        if scheme == "dpapi" and sys.platform == "win32":
            return _dpapi(base64.b64decode(payload), False).decode("utf-8")
        if scheme == "fernet":
            return _fernet().decrypt(payload.encode("ascii")).decode("utf-8")
    except SecureStoreError:
        raise
    except Exception as e:
        raise SecureStoreError(f"Could not decrypt saved password: {e}")
    raise SecureStoreError("Saved password was encrypted on a different system.")
