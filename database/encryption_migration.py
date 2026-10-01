"""Create and verify an encrypted copy of an existing plaintext SQLite database."""
import argparse
import os
import re
import secrets
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlite3


_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def encrypt_database(source_path: str | Path, destination_path: str | Path, key: str) -> Path:
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    if source == destination:
        raise ValueError("Source and destination must be different files.")
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError("Encryption key must be 64 hexadecimal characters.")
    if not destination.parent.is_dir():
        raise FileNotFoundError(destination.parent)

    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    connection = None
    try:
        connection = sqlite3.connect(str(source))
        quoted_path = str(temporary).replace("'", "''")
        normalized_key = key.lower()
        connection.execute(
            f'ATTACH DATABASE \'{quoted_path}\' AS encrypted KEY "x\'{normalized_key}\'"'
        )
        connection.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        connection.execute("DETACH DATABASE encrypted")
        connection.close()
        connection = None

        verification = sqlite3.connect(str(temporary))
        try:
            verification.execute(f'PRAGMA key = "x\'{normalized_key}\'"')
            result = verification.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Encrypted database failed its integrity check.")
        finally:
            verification.close()

        temporary.rename(destination)
        return destination
    except Exception:
        if connection is not None:
            connection.close()
        for path in (temporary, Path(f"{temporary}-wal"), Path(f"{temporary}-shm")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a verified SQLCipher-encrypted copy without modifying the source database."
    )
    parser.add_argument("source", help="Existing plaintext SQLite database")
    parser.add_argument("destination", help="New encrypted database path; must not already exist")
    args = parser.parse_args()

    key = os.getenv("DATABASE_ENCRYPTION_KEY", "").strip()
    if not _KEY_PATTERN.fullmatch(key):
        parser.error("DATABASE_ENCRYPTION_KEY must be set to a 64-character hex key in the process environment.")

    output = encrypt_database(args.source, args.destination, key)
    print(f"Encrypted database created and verified: {output}")
    print("The plaintext source was not changed; securely retire it after cutover and backup verification.")


if __name__ == "__main__":
    main()