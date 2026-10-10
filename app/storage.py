"""Secure Persistent Storage using Fernet symmetric encryption and atomic file operations."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, Optional
import uuid

from cryptography.fernet import Fernet, InvalidToken

from sniper.models import TaskModel, TaskStatus

logger = logging.getLogger("app.storage")


def _resolve_or_create_key(key_path: Path) -> bytes:
    """Resolve Fernet key from environment variable, existing key file, or generate a new one."""
    env_key = os.environ.get("SNIPER_MASTER_KEY")
    if env_key:
        try:
            key_bytes = env_key.strip().encode("utf-8")
            # Validate key with Fernet
            Fernet(key_bytes)
            logger.info("Loaded master key from SNIPER_MASTER_KEY environment variable.")
            return key_bytes
        except Exception as e:
            logger.warning(
                "SNIPER_MASTER_KEY is invalid (%s); falling back to file key.", e
            )

    if key_path.exists():
        try:
            key_bytes = key_path.read_bytes().strip()
            Fernet(key_bytes)
            logger.info("Loaded master key from file: %s", key_path)
            return key_bytes
        except Exception as e:
            logger.warning(
                "Key file %s is corrupt or invalid (%s); generating new key.",
                key_path,
                e,
            )

    # Generate fresh key
    new_key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(new_key)
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass
    logger.info("Generated new master key at: %s", key_path)
    return new_key


class EncryptedStorage:
    """Thread-safe and async-safe encrypted JSON storage for sniper tasks.

    Uses Fernet authenticated encryption (AES-128-CBC + HMAC-SHA256) with atomic
    file replacements (.tmp -> os.replace) and automatic corruption recovery.
    """

    def __init__(
        self,
        filepath: Path = Path("data/tasks.enc.json"),
        key_path: Path = Path("data/secret.key"),
        master_key: Optional[bytes] = None,
    ) -> None:
        self.filepath = Path(filepath)
        self.key_path = Path(key_path)
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.RLock()

        if master_key is not None:
            Fernet(master_key)  # validate
            self._key = master_key
        else:
            self._key = _resolve_or_create_key(self.key_path)

        self._fernet = Fernet(self._key)

    @property
    def key(self) -> bytes:
        """Return the active Fernet key."""
        return self._key

    def _handle_corrupt_file(self) -> None:
        """Move corrupted or undecryptable file to backup with timestamp."""
        if not self.filepath.exists():
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        corrupt_backup = (
            self.filepath.parent / f"{self.filepath.name}.corrupt.{timestamp}"
        )
        try:
            shutil.move(str(self.filepath), str(corrupt_backup))
            logger.critical(
                "Corrupted storage file backed up to %s. Resetting to empty store.",
                corrupt_backup,
            )
        except Exception as e:
            logger.error("Failed to backup corrupt file %s: %s", self.filepath, e)

    def load_all(self) -> Dict[str, TaskModel]:
        """Read and decrypt all persisted tasks, sanitizing active states on startup."""
        with self._thread_lock:
            if not self.filepath.exists():
                return {}

            try:
                ciphertext = self.filepath.read_bytes()
                if not ciphertext:
                    return {}
                plaintext = self._fernet.decrypt(ciphertext)
                data = json.loads(plaintext.decode("utf-8"))
            except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.error(
                    "Decryption or JSON parsing failed for %s (%s). Initiating recovery.",
                    self.filepath,
                    e,
                )
                self._handle_corrupt_file()
                return {}
            except Exception as e:
                logger.error("Unexpected error reading storage file: %s", e)
                return {}

            # Handle format variants: {"tasks": {...}} or direct dict {...}
            raw_tasks = data.get("tasks", data) if isinstance(data, dict) else {}
            if not isinstance(raw_tasks, dict):
                logger.warning("Invalid storage structure. Expected dict of tasks.")
                return {}

            tasks: Dict[str, TaskModel] = {}
            for task_id, task_raw in raw_tasks.items():
                try:
                    if not isinstance(task_raw, dict):
                        continue
                    task = TaskModel.model_validate(task_raw)
                    # Sanitize any in-flight active statuses to STOPPED upon reload
                    if task.status in (
                        TaskStatus.POLLING,
                        TaskStatus.SNIPING,
                        TaskStatus.WAITING_LOGIN,
                        TaskStatus.PENDING,
                    ):
                        task.status = TaskStatus.STOPPED
                    tasks[task.id] = task
                except Exception as err:
                    logger.warning("Failed to parse task %s: %s", task_id, err)

            return tasks

    def save_all(self, tasks: Dict[str, TaskModel]) -> None:
        """Encrypt in-memory and write atomically to disk."""
        with self._thread_lock:
            raw_tasks: Dict[str, Any] = {}
            for tid, t in tasks.items():
                if isinstance(t, TaskModel):
                    raw_tasks[tid] = t.model_dump(mode="json")
                elif isinstance(t, dict):
                    raw_tasks[tid] = t
                else:
                    raw_tasks[tid] = TaskModel.model_validate(t).model_dump(mode="json")

            payload = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "tasks": raw_tasks,
            }
            plaintext = json.dumps(payload, indent=2, ensure_ascii=False).encode(
                "utf-8"
            )
            ciphertext = self._fernet.encrypt(plaintext)

            # Atomic write via unique temporary file
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = (
                self.filepath.parent
                / f"{self.filepath.name}.tmp.{uuid.uuid4().hex[:8]}"
            )
            try:
                tmp_path.write_bytes(ciphertext)
                os.replace(str(tmp_path), str(self.filepath))
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

    def get_task(self, task_id: str) -> Optional[TaskModel]:
        """Retrieve a task by ID or return None."""
        tasks = self.load_all()
        return tasks.get(task_id)

    def save_task(self, task: TaskModel) -> None:
        """Create or update a single task definition."""
        with self._thread_lock:
            tasks = self.load_all()
            tasks[task.id] = task
            self.save_all(tasks)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task by ID. Returns True if task existed and was deleted."""
        with self._thread_lock:
            tasks = self.load_all()
            if task_id in tasks:
                del tasks[task_id]
                self.save_all(tasks)
                return True
            return False
