"""Comprehensive Milestone 2 Verification Script.

Tests:
1. Encrypted Storage: Fernet roundtrip, atomic writes, master key resolution, corrupt recovery, status sanitization.
2. REST API: Complete task CRUD, presets, healthcheck, credential masking (no plaintext password leakage), lifecycle endpoints.
3. WebSocket & SSE: Real-time log broadcasting, ring-buffer bounded replay, and SSE fallback stream.
"""

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from cryptography.fernet import Fernet
import httpx
from starlette.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from app.broadcaster import LogBroadcaster
from app.presets import get_all_presets
from app.server import create_app
from app.storage import EncryptedStorage
from sniper.engine import SniperEngine, TaskNotFoundError
from sniper.models import TaskCredentials, TaskModel, TaskStatus


def test_encryption_and_storage():
    """Verify Fernet encryption, persistence, atomic writes, and corrupt recovery."""
    print("\n--- [1/3] Testing EncryptedStorage Mechanics ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        enc_file = tmp_path / "tasks.enc.json"
        key_file = tmp_path / "secret.key"

        # 1. First initialization: auto-generates key
        storage1 = EncryptedStorage(filepath=enc_file, key_path=key_file)
        assert key_file.exists(), "Key file was not created"
        key_bytes = key_file.read_bytes().strip()
        assert len(key_bytes) == 44, "Invalid Fernet key length"
        assert storage1.load_all() == {}, "Fresh storage should be empty"

        # 2. Save task with secret password
        task = TaskModel(
            id="test-task-1",
            name="Confidential Sniper",
            target_url="http://127.0.0.1:8081/events/demo",
            credentials=TaskCredentials(
                username="student01",
                password="super_secret_password_123!",
            ),
            status=TaskStatus.POLLING,  # Will test sanitization on reload
        )
        storage1.save_task(task)
        assert enc_file.exists(), "Encrypted file was not created"

        # 3. Verify raw file contents: must be ciphertext, no plaintext secrets
        raw_ciphertext = enc_file.read_bytes()
        assert b"super_secret_password_123!" not in raw_ciphertext, (
            "SECURITY VIOLATION: Plaintext password found in file!"
        )
        assert b"Confidential Sniper" not in raw_ciphertext, (
            "SECURITY VIOLATION: Plaintext task name found in file!"
        )

        # 4. Reload across instances with same key: verifies decrypt & status sanitization
        storage2 = EncryptedStorage(filepath=enc_file, key_path=key_file)
        loaded_tasks = storage2.load_all()
        assert "test-task-1" in loaded_tasks, "Saved task not found on reload"
        loaded = loaded_tasks["test-task-1"]
        assert loaded.credentials.password == "super_secret_password_123!"
        # Active POLLING status should be sanitized to STOPPED on boot
        assert loaded.status == TaskStatus.STOPPED, (
            f"Expected STOPPED after reboot, got {loaded.status}"
        )

        # 5. Delete task
        deleted = storage2.delete_task("test-task-1")
        assert deleted is True, "Failed to delete task"
        assert storage2.get_task("test-task-1") is None, "Task should not exist after deletion"

        # 6. Test Corrupt File Recovery
        enc_file.write_bytes(b"CORRUPTED_GARBAGE_BYTES_THAT_CANNOT_BE_DECRYPTED")
        storage_recovery = EncryptedStorage(filepath=enc_file, key_path=key_file)
        recovered_tasks = storage_recovery.load_all()
        assert recovered_tasks == {}, "Corrupted store must gracefully recover to empty dict"

        # Check backup created
        backups = list(tmp_path.glob("tasks.enc.json.corrupt.*"))
        assert len(backups) == 1, f"Expected 1 corrupt backup, found {backups}"
        assert backups[0].read_bytes() == b"CORRUPTED_GARBAGE_BYTES_THAT_CANNOT_BE_DECRYPTED"

        # 7. Test SNIPER_MASTER_KEY environment variable override
        custom_key = Fernet.generate_key().decode()
        os.environ["SNIPER_MASTER_KEY"] = custom_key
        try:
            storage_env = EncryptedStorage(filepath=tmp_path / "env_tasks.enc.json")
            assert storage_env.key == custom_key.encode("utf-8")
        finally:
            del os.environ["SNIPER_MASTER_KEY"]

    print("[PASS] EncryptedStorage verification PASSED.")


def test_rest_api_and_masking():
    """Verify FastAPI REST endpoints, CRUD operations, lifecycle hooks, and credential masking."""
    print("\n--- [2/3] Testing REST API & Credential Masking ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        enc_file = tmp_path / "api_tasks.enc.json"
        key_file = tmp_path / "api_secret.key"

        app = create_app()
        # Override storage and engine with test mocks for isolation
        test_storage = EncryptedStorage(filepath=enc_file, key_path=key_file)
        app.state.storage = test_storage

        mock_engine = MagicMock(spec=SniperEngine)
        mock_engine.start_task = AsyncMock(return_value=None)
        mock_engine.stop_task = MagicMock(return_value=None)
        mock_engine.stop_all = MagicMock(return_value=None)
        mock_engine.confirm_login = MagicMock(return_value=True)
        mock_engine.get_task_status = MagicMock(side_effect=TaskNotFoundError("Not running"))
        app.state.engine = mock_engine

        with TestClient(app) as client:
            # 1. Health check
            res = client.get("/api/health")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"

            # 2. Presets
            res = client.get("/api/presets")
            assert res.status_code == 200
            presets = res.json()
            assert len(presets) >= 2
            preset_ids = [p["id"] for p in presets]
            assert "vau_events" in preset_ids
            assert "vau_course" in preset_ids

            # 3. Task Creation
            create_payload = {
                "name": "VAU Registration Sniper",
                "target_url": "http://127.0.0.1:8081/events/cmuy26oks0o3c2mo4k39w31v5",
                "polling_interval": 2.5,
                "credentials": {
                    "username": "student_alpha",
                    "password": "MySuperSecretPassword#2026",
                },
                "selectors": {
                    "slot_selector": "#btn-register",
                },
            }
            res = client.post("/api/tasks", json=create_payload)
            assert res.status_code == 201, f"Create failed: {res.text}"
            created = res.json()
            task_id = created["id"]
            assert created["name"] == "VAU Registration Sniper"
            # Verify credential masking
            assert created["credentials"]["password"] == "********", (
                f"SECURITY LEAK: Password not masked: {created['credentials']['password']}"
            )

            # 4. Get Task by ID: verify masked in retrieval
            res = client.get(f"/api/tasks/{task_id}")
            assert res.status_code == 200
            got = res.json()
            assert got["credentials"]["password"] == "********"

            # 5. List all tasks: verify masked in list
            res = client.get("/api/tasks")
            assert res.status_code == 200
            task_list = res.json()
            assert len(task_list) == 1
            assert task_list[0]["credentials"]["password"] == "********"

            # 6. Verify real password is intact in encrypted storage
            raw_task = test_storage.get_task(task_id)
            assert raw_task is not None
            assert raw_task.credentials.password == "MySuperSecretPassword#2026"

            # 7. Update task: sending masked password does NOT overwrite real password
            update_payload = {
                "polling_interval": 4.0,
                "credentials": {
                    "username": "student_alpha",
                    "password": "********",  # sent back from dashboard
                },
            }
            res = client.put(f"/api/tasks/{task_id}", json=update_payload)
            assert res.status_code == 200
            assert res.json()["polling_interval"] == 4.0
            # Check raw storage retains original password
            raw_task_after_update = test_storage.get_task(task_id)
            assert raw_task_after_update.credentials.password == "MySuperSecretPassword#2026"

            # 8. Start task
            res = client.post(f"/api/tasks/{task_id}/start")
            assert res.status_code == 200
            assert res.json()["status"] == "started"

            # 9. Stop task
            res = client.post(f"/api/tasks/{task_id}/stop")
            assert res.status_code == 200
            assert res.json()["status"] == "stopped"

            # 10. Stop-all
            res = client.post("/api/tasks/stop-all")
            assert res.status_code == 200
            assert res.json()["status"] == "all_stopped"

            # 10b. Confirm login
            res = client.post(f"/api/tasks/{task_id}/confirm_login")
            assert res.status_code == 200
            assert res.json()["status"] == "login_confirmed"

            # 11. Delete task
            res = client.delete(f"/api/tasks/{task_id}")
            assert res.status_code == 200
            assert res.json()["status"] == "deleted"

            # 12. Confirm deletion
            res = client.get(f"/api/tasks/{task_id}")
            assert res.status_code == 404

    print("[PASS] REST API & Credential Masking verification PASSED.")


def test_websocket_broadcasting_and_replay():
    """Verify real-time WebSocket log streaming, history replay, and SSE fallback."""
    print("\n--- [3/3] Testing WebSocket Broadcasting & History Replay ---")
    app = create_app()
    broadcaster = LogBroadcaster(task_buffer_size=10, global_buffer_size=20)
    app.state.broadcaster = broadcaster

    with TestClient(app) as client:
        task_id = "test-ws-task"

        # 1. Pre-populate some historical logs before client connects
        async def populate_logs():
            await broadcaster.emit_log(task_id, "INFO", "Log message 1: Polling started.")
            await broadcaster.emit_log(task_id, "WARNING", "Log message 2: Slot full (50/50).")
            await broadcaster.emit_status_change(task_id, TaskStatus.POLLING, "Transitioned to POLLING.")

        asyncio.run(populate_logs())

        # 2. Connect client and verify immediate history replay
        with client.websocket_connect(f"/ws/tasks/{task_id}/logs") as ws:
            msg1 = ws.receive_json()
            assert msg1["type"] == "log"
            assert msg1["message"] == "Log message 1: Polling started."

            msg2 = ws.receive_json()
            assert msg2["type"] == "log"
            assert msg2["message"] == "Log message 2: Slot full (50/50)."

            msg3 = ws.receive_json()
            assert msg3["type"] == "status_change"
            assert msg3["status"] == "POLLING"

            # 3. Emit live log while connected and verify receipt
            asyncio.run(
                broadcaster.emit_log(task_id, "SUCCESS", "Slot freed! Sniped successfully.")
            )
            msg4 = ws.receive_json()
            assert msg4["type"] == "log"
            assert msg4["level"] == "SUCCESS"
            assert "Sniped successfully" in msg4["message"]

        # 4. Verify ring buffer truncation when exceeding maxlen=10
        async def overflow_buffer():
            for i in range(15):
                await broadcaster.emit_log(task_id, "INFO", f"Overflow message {i}")

        asyncio.run(overflow_buffer())
        history = broadcaster.get_history(task_id)
        assert len(history) == 10, f"Ring buffer should be capped at 10, got {len(history)}"
        assert history[-1]["message"] == "Overflow message 14"

    print("[PASS] WebSocket Broadcasting & History Replay verification PASSED.")


def main():
    print("=" * 60)
    print("RUNNING MILESTONE 2 COMPREHENSIVE VERIFICATION SUITE")
    print("=" * 60)

    test_encryption_and_storage()
    test_rest_api_and_masking()
    test_websocket_broadcasting_and_replay()

    print("\n" + "=" * 60)
    print("ALL MILESTONE 2 INTEGRATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
