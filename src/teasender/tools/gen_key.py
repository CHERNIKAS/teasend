"""CLI: generate the Fernet key that encrypts the session at rest.

    python -m teasender.tools.gen_key
"""
from __future__ import annotations

from teasender.config import get_settings
from teasender.core.security import generate_key


def main() -> None:
    path = generate_key(get_settings().secret_key_file)
    print(f"Wrote encryption key to {path}")
    print("Back this up separately from the database. Never commit it.")


if __name__ == "__main__":
    main()
