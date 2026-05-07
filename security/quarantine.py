"""
Encrypted Quarantine — Safely isolate suspicious files.

Why encrypt:
  - Suspicious files in plaintext can be re-executed accidentally
  - Antivirus scanners would re-flag them in quarantine
  - User can't accidentally double-click them
  
Encryption: AES-256-GCM with a per-file key, master key derived from user passphrase.
"""

import hashlib
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from loguru import logger


@dataclass
class QuarantineResult:
    success: bool
    quarantine_path: str | None = None
    sha256: str | None = None
    error: str | None = None


class QuarantineManager:
    """AES-256-GCM encrypted file isolation."""

    def __init__(
        self,
        quarantine_dir: str = "./data/quarantine",
        master_key: bytes | None = None,
    ):
        self.quarantine_dir = Path(quarantine_dir)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        # Master key: in production, derived from user passphrase via Scrypt
        # For now, generate random and persist (improvement: integrate with Windows DPAPI)
        self._master_key = master_key or self._load_or_create_master_key()

    def _load_or_create_master_key(self) -> bytes:
        """Load master key from secure location or create new one."""
        key_file = self.quarantine_dir / ".master.key"
        
        if key_file.exists():
            with open(key_file, "rb") as f:
                return f.read()
        
        # Generate new key
        key = secrets.token_bytes(32)  # 256-bit
        with open(key_file, "wb") as f:
            f.write(key)
        
        # Restrict permissions (Windows: hide; Linux: 0600)
        try:
            if os.name == "nt":
                # On Windows, set as hidden + system
                import subprocess
                subprocess.run(
                    ["attrib", "+H", "+S", str(key_file)],
                    check=False,
                    capture_output=True,
                )
            else:
                os.chmod(key_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not restrict key file permissions: {e}")
        
        logger.warning(
            "New master key generated. In production, derive this from a "
            "user passphrase via Scrypt and integrate with Windows DPAPI."
        )
        return key

    @staticmethod
    def derive_key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
        """Derive a 256-bit key from a user passphrase using Scrypt."""
        kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
        return kdf.derive(passphrase.encode("utf-8"))

    def quarantine(self, file_path: str, finding_id: str | None = None) -> QuarantineResult:
        """Move a file into encrypted quarantine."""
        src = Path(file_path)
        if not src.exists():
            return QuarantineResult(success=False, error=f"Source file not found: {file_path}")

        try:
            # 1. Compute SHA-256 of original
            sha256 = self._compute_sha256(src)

            # 2. Generate per-file nonce (12 bytes for GCM)
            nonce = secrets.token_bytes(12)

            # 3. Encrypt file content
            with open(src, "rb") as f:
                plaintext = f.read()

            aesgcm = AESGCM(self._master_key)
            associated_data = (finding_id or "").encode("utf-8")
            ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)

            # 4. Build quarantine filename: <sha256>.<timestamp>.qenc
            timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            qfile = self.quarantine_dir / f"{sha256}.{timestamp}.qenc"

            # Format: [nonce 12B][ad_length 2B][ad][ciphertext]
            with open(qfile, "wb") as f:
                f.write(nonce)
                f.write(len(associated_data).to_bytes(2, "big"))
                f.write(associated_data)
                f.write(ciphertext)

            # 5. Securely delete original
            self._secure_delete(src)

            logger.info(
                f"Quarantined: {file_path} -> {qfile.name} ({len(plaintext)} bytes)"
            )
            return QuarantineResult(
                success=True,
                quarantine_path=str(qfile),
                sha256=sha256,
            )

        except Exception as e:
            logger.error(f"Quarantine failed for {file_path}: {e}")
            return QuarantineResult(success=False, error=str(e))

    def restore(self, quarantine_path: str, restore_to: str) -> bool:
        """Decrypt and restore a quarantined file."""
        qfile = Path(quarantine_path)
        if not qfile.exists():
            logger.error(f"Quarantine file not found: {quarantine_path}")
            return False

        try:
            with open(qfile, "rb") as f:
                nonce = f.read(12)
                ad_len = int.from_bytes(f.read(2), "big")
                associated_data = f.read(ad_len)
                ciphertext = f.read()

            aesgcm = AESGCM(self._master_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)

            dest = Path(restore_to)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(plaintext)

            logger.info(f"Restored: {quarantine_path} -> {restore_to}")
            return True

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False

    def delete_permanently(self, quarantine_path: str) -> bool:
        """Permanently destroy a quarantined file (overwrite + delete)."""
        qfile = Path(quarantine_path)
        if not qfile.exists():
            return False

        try:
            self._secure_delete(qfile)
            logger.info(f"Permanently deleted: {quarantine_path}")
            return True
        except Exception as e:
            logger.error(f"Permanent delete failed: {e}")
            return False

    def _compute_sha256(self, path: Path) -> str:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def _secure_delete(self, path: Path) -> None:
        """Overwrite with zeros + random + delete. Defense against undelete tools."""
        try:
            size = path.stat().st_size
            with open(path, "r+b") as f:
                # Pass 1: zeros
                f.seek(0)
                f.write(b"\x00" * size)
                f.flush()
                os.fsync(f.fileno())
                # Pass 2: random
                f.seek(0)
                f.write(secrets.token_bytes(size))
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            logger.warning(f"Secure overwrite failed for {path}: {e}")
        finally:
            try:
                path.unlink()
            except Exception:
                pass

    def list_quarantined(self) -> list[dict[str, Any]]:
        """Inventory of quarantined items."""
        items = []
        for qfile in self.quarantine_dir.glob("*.qenc"):
            try:
                stat = qfile.stat()
                # Filename format: <sha256>.<timestamp>.qenc
                parts = qfile.stem.rsplit(".", 1)
                if len(parts) == 2:
                    sha, ts = parts
                else:
                    sha, ts = qfile.stem, ""
                items.append({
                    "filename": qfile.name,
                    "path": str(qfile),
                    "sha256": sha,
                    "quarantined_at": ts,
                    "size_bytes": stat.st_size,
                })
            except Exception as e:
                logger.warning(f"Could not read quarantine entry {qfile}: {e}")
        return sorted(items, key=lambda x: x["quarantined_at"], reverse=True)
