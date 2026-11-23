"""Local named credentials for the OpenAI provider. Public references are endpoint-bound."""

import ctypes
import os
from pathlib import Path
import sqlite3
import uuid
from contextlib import closing, contextmanager
from urllib.parse import urlsplit, urlunsplit

DEFAULT_URL = "https://api.openai.com/v1"
KEY_REF = "pdf2zh-key:"


def endpoint(value):
    parts = urlsplit((value or DEFAULT_URL).strip())
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("请填写有效的 HTTP(S) API Base URL。")
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def _protect(data, decrypt=False):
    if os.name != "nt":
        return data
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buffer), Blob()
    if not operation(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)
    ):
        raise ValueError("Windows 无法访问此用户的已保存密钥。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class OpenAIKeys:
    def __init__(self, config_path):
        self.path = Path(config_path).with_suffix(".openai-keys.sqlite3")

    @contextmanager
    def _connect(self):
        # Restrict POSIX permissions before SQLite creates a journal or stores a key.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT NOT NULL, secret BLOB NOT NULL)"
            )
            yield db

    def choices(self):
        if not self.path.is_file():
            return []
        with self._connect() as db:
            return db.execute("SELECT name, id FROM profiles ORDER BY rowid").fetchall()

    def profile(self, ref):
        with self._connect() as db:
            row = db.execute(
                "SELECT name, url FROM profiles WHERE id=?", (ref,)
            ).fetchone()
        if row is None:
            raise ValueError("找不到此密钥，请重新选择或输入。")
        return row

    def resolve(self, url, value):
        value = (value or "").strip()
        if not value.startswith(KEY_REF):
            if not value or value == "***":
                raise ValueError("请先输入或选择 API 密钥。")
            return value
        with self._connect() as db:
            row = db.execute(
                "SELECT secret FROM profiles WHERE id=? AND url=?",
                (value, endpoint(url)),
            ).fetchone()
        if row is None:
            raise ValueError("此密钥与当前 Base URL 不匹配，请重新选择或输入。")
        return _protect(row[0], decrypt=True).decode()

    def remember(self, url, value, name=""):
        url, value, name = endpoint(url), (value or "").strip(), (name or "").strip()
        if not value or value == "***":
            raise ValueError("请先输入或选择 API 密钥。")
        if len(name) > 80 or any(not ch.isprintable() for ch in name):
            raise ValueError("请使用 1 至 80 个可打印字符作为密钥名称。")
        if name.startswith(KEY_REF) or (
            name and (name.startswith("sk-") or name == value)
        ):
            raise ValueError("请使用便于辨认的名称，不能将密钥用作名称。")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if value.startswith(KEY_REF):
                row = db.execute(
                    "SELECT id FROM profiles WHERE id=? AND url=?", (value, url)
                ).fetchone()
                if row is None:
                    raise ValueError("此密钥与当前 Base URL 不匹配，请重新选择或输入。")
                ref = row[0]
            else:
                data = value.encode()
                ref = next(
                    (
                        ident
                        for ident, secret in db.execute(
                            "SELECT id,secret FROM profiles WHERE url=?", (url,)
                        )
                        if _protect(secret, decrypt=True) == data
                    ),
                    None,
                )
            try:
                if ref:
                    if name:
                        db.execute("UPDATE profiles SET name=? WHERE id=?", (name, ref))
                else:
                    ref = KEY_REF + uuid.uuid4().hex
                    if not name:
                        seq = (
                            db.execute("SELECT count(*) FROM profiles").fetchone()[0]
                            + 1
                        )
                        name = f"OpenAI {seq}"
                        while db.execute(
                            "SELECT 1 FROM profiles WHERE name=?", (name,)
                        ).fetchone():
                            seq += 1
                            name = f"OpenAI {seq}"
                    db.execute(
                        "INSERT INTO profiles VALUES (?,?,?,?)",
                        (ref, name, url, _protect(data)),
                    )
            except sqlite3.IntegrityError:
                raise ValueError("此名称已使用，请换一个名称。") from None
        return ref
