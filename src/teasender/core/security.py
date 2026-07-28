"""Secret handling.

The Telethon *session string* grants full control of the account, so it is never
stored in plaintext. It is encrypted with a Fernet key kept in a separate file
(`SECRET_KEY_FILE`) that is never committed.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretError(RuntimeError):
    pass


def generate_key(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SecretError(f"key already exists at {path}; refusing to overwrite")
    path.write_bytes(Fernet.generate_key())
    _lock_down(path)
    return path


def _lock_down(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 on POSIX
    except (OSError, NotImplementedError):
        pass  # Windows: rely on ACLs + .gitignore (see docs/SECURITY.md)


def load_cipher(path: Path) -> Fernet:
    if not path.exists():
        raise SecretError(
            f"secret key not found at {path}. Run: python -m teasender.tools.gen_key"
        )
    return Fernet(path.read_bytes())


def save_session(cipher: Fernet, session_string: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cipher.encrypt(session_string.encode("utf-8")))
    _lock_down(path)


def load_session(cipher: Fernet, path: Path) -> str:
    if not path.exists():
        raise SecretError(
            f"no session at {path}. Run: python -m teasender.tools.login"
        )
    try:
        return cipher.decrypt(path.read_bytes()).decode("utf-8")
    except InvalidToken as exc:
        raise SecretError("failed to decrypt session (wrong key?)") from exc
