"""
Tier 5 Adversarial Test Suite: Full Stack Integration, Storage Tamper Resistance,
and Concurrent Multi-Task Stress Harness.

Verifies:
1. Full end-to-end integration: Create task via FastAPI REST API -> Verify encrypted
   persistence on disk -> Start task via API -> Verify live logs streamed over WebSocket
   -> Mock server frees slot -> Sniper detects and executes dual-trigger -> Task status
   reaches SUCCESS -> Encrypted storage updated -> Verify Web Audio event / WebSocket event.
2. Storage tamper resistance: Corrupt ciphertext on disk (raw noise, truncated tokens,
   malformed JSON in valid ciphertext) and verify API auto-recovers gracefully without crashing.
3. Concurrent multi-task stress: 3 concurrent tasks targeting different mock endpoints
   running simultaneously without session collisions or cookie cross-contamination.
4. Slot contention race: 3 concurrent tasks competing for a single freed slot, verifying
   exactly 1 winner and 0 false-positive SUCCESS transitions.
"""

import asyncio
from functools import wraps
import json
import logging
from pathlib import Path
import socket
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from cryptography.fernet import Fernet
import httpx
import pytest
import uvicorn
import websockets
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.broadcaster import LogBroadcaster
from app.server import create_app
from app.storage import EncryptedStorage
from sniper.engine import SniperEngine
from sniper.models import (
    ApiTriggerConfig,
    LogEntry,
    TaskCredentials,
    TaskModel,
    TaskSelectors,
    TaskStatus,
)
from tests.mock_target_server import (
    MockPortalState,
    MockServerHandle,
    mock_state,
    render_html_portal,
    start_mock_server,
)

logger = logging.getLogger("test_adversarial_tier5")


def get_free_port() -> int:
    """Find a dynamically available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def async_test(f):
    """Decorator to run async test functions synchronously via asyncio.run."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


class UvicornServerHandle:
    """Handle to cleanly start and stop background uvicorn servers."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread, port: int):
        self.server = server
        self.thread = thread
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.ws_url = f"ws://127.0.0.1:{port}"

    def stop(self):
        """Stop uvicorn server and join thread."""
        self.server.should_exit = True
        self.thread.join(timeout=4.0)


def start_test_server(app: FastAPI, port: Optional[int] = None) -> UvicornServerHandle:
    """Start FastAPI application in a dedicated background daemon thread."""
    assigned_port = port or get_free_port()
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=assigned_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to bind and be ready
    for _ in range(60):
        if server.started:
            break
        time.sleep(0.05)

    return UvicornServerHandle(server=server, thread=thread, port=assigned_port)


# ==============================================================================
# Multi-Event Portal Mock Server for Multi-Task Concurrency Stress
# ==============================================================================

class MultiPortalServer:
    """Mock server managing multiple independent event registration portals."""

    def __init__(self, event_ids: List[str]):
        self.event_ids = event_ids
        self.states: Dict[str, MockPortalState] = {
            eid: MockPortalState(total_slots=50, initial_full=True, auto_release_after=0.0)
            for eid in event_ids
        }
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Multi-Event Mock Portal")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        class MultiRegisterPayload(BaseModel):
            sessionIds: List[str] = Field(default_factory=lambda: ["slot-alpha"])

        @app.get("/events/{event_id}", response_class=HTMLResponse)
        async def get_event_page(event_id: str):
            if event_id not in self.states:
                raise HTTPException(status_code=404, detail="Event not found")
            st = self.states[event_id]
            st.check_dynamic_release()
            status_dict = st.get_status_dict()
            is_full = status_dict["is_full"]
            occupied = status_dict["occupied_slots"]
            total = status_dict["total_slots"]

            if is_full:
                banner_class = "status-full"
                banner_text = f"Trạng thái: Đã hết chỗ ({occupied}/{total})"
                button_html = '<button id="btn-register" disabled class="btn-disabled bg-gray-600 text-gray-300 font-bold py-3 px-6 rounded-lg cursor-not-allowed">Hết chỗ</button>'
            else:
                banner_class = "status-available"
                banner_text = f"Trạng thái: Còn chỗ trống ({occupied}/{total})"
                button_html = f'<button id="btn-register" class="btn-register bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-6 rounded-lg shadow-lg" onclick="openRegisterModal()">Đăng ký tham gia</button>'

            html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Event {event_id}</title>
</head>
<body class="p-6">
    <div id="status-banner" class="{banner_class}">{banner_text}</div>
    <div id="action-container">{button_html}</div>
    <div id="register-modal" class="hidden">
        <input type="checkbox" id="session-pick-{event_id}" name="session" value="{event_id}-session" checked>
        <button id="btn-confirm-register" onclick="submitRegistration()">Xác nhận đăng ký</button>
    </div>
    <script>
        function openRegisterModal() {{
            document.getElementById('register-modal').classList.remove('hidden');
        }}
        async function submitRegistration() {{
            try {{
                const res = await fetch('/api/events/{event_id}/register', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ sessionIds: ['{event_id}-session'] }})
                }});
            }} catch (e) {{}}
        }}
    </script>
</body>
</html>"""
            return HTMLResponse(content=html)

        @app.post("/api/events/{event_id}/register")
        async def register_event(event_id: str, payload: MultiRegisterPayload, request: Request):
            if event_id not in self.states:
                raise HTTPException(status_code=404, detail="Event not found")
            st = self.states[event_id]
            cookies = dict(request.cookies)
            cookie_header = request.headers.get("cookie", "")
            res = st.register(
                session_ids=payload.sessionIds,
                method="api",
                user_info={"cookies": cookies, "cookie_header": cookie_header},
            )
            if not res["success"]:
                raise HTTPException(status_code=res["status_code"], detail=res)
            return JSONResponse(status_code=200, content=res)

        @app.post("/mock/release/{event_id}")
        async def release_event_slot(event_id: str):
            if event_id not in self.states:
                raise HTTPException(status_code=404, detail="Event not found")
            st = self.states[event_id]
            res = st.release_slot()
            return JSONResponse(status_code=200, content={"event_id": event_id, **res})

        @app.get("/mock/status/{event_id}")
        async def get_event_status(event_id: str):
            if event_id not in self.states:
                raise HTTPException(status_code=404, detail="Event not found")
            return JSONResponse(status_code=200, content=self.states[event_id].get_status_dict())

        return app


# ==============================================================================
# TEST 1: Full End-to-End Integration Flow
# ==============================================================================

@async_test
async def test_tier5_full_e2e_integration_flow():
    """
    Tier 5 Primary Acceptance Test:
    1. Create task via FastAPI REST API (POST /api/tasks).
    2. Verify encrypted persistence on disk (tasks.enc.json contains ciphertext, no plaintext secrets).
    3. Start task via API (POST /api/tasks/{task_id}/start).
    4. Connect WebSocket client (/ws/tasks/{task_id}/logs and /ws/logs) and verify live log streaming.
    5. Mock server frees slot (POST /mock/release-slot).
    6. Sniper detects slot and executes dual-trigger (API fetch + UI click).
    7. Task status transitions to SUCCESS.
    8. Encrypted storage updated on disk with TaskStatus.SUCCESS.
    9. Verify Web Audio trigger / WebSocket event dispatched (type: 'status_change', status: 'SUCCESS').
    """
    # 1. Start mock target portal on isolated port
    mock_port = get_free_port()
    mock_handle = start_mock_server(port=mock_port)
    mock_url = f"http://127.0.0.1:{mock_port}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        enc_file = tmp_path / "tasks.enc.json"
        key_file = tmp_path / "secret.key"

        # 2. Build isolated FastAPI backend app
        app = create_app()
        storage = EncryptedStorage(filepath=enc_file, key_path=key_file)
        broadcaster = LogBroadcaster()
        engine = SniperEngine()

        app.state.storage = storage
        app.state.broadcaster = broadcaster
        app.state.engine = engine

        backend_port = get_free_port()
        backend_handle = start_test_server(app=app, port=backend_port)
        backend_url = backend_handle.base_url

        try:
            # 3. Create task via REST API
            async with httpx.AsyncClient(base_url=backend_url) as client:
                # Check backend health
                health_res = await client.get("/api/health")
                assert health_res.status_code == 200
                assert health_res.json()["status"] == "ok"

                create_payload = {
                    "name": "Tier5 Dual-Trigger Master Sniper",
                    "target_url": f"{mock_url}/events/mock-event-vau",
                    "polling_interval": 0.35,
                    "jitter_sec": 0.05,
                    "wait_condition": "domcontentloaded",
                    "block_media_assets": True,
                    "headless": True,
                    "manual_login": False,
                    "detection_type": "DOM_SELECTOR",
                    "credentials": {
                        "username": "student_tier5_specialist",
                        "password": "UltraSecretP@ssword2026!",
                    },
                    "selectors": {
                        "slot_selector": "#btn-register",
                        "slot_keyword": "Đăng ký tham gia",
                        "full_keyword": "Hết chỗ",
                        "ui_slot_selector": "#btn-register",
                        "ui_submit_selector": "#btn-confirm-register",
                    },
                    "api_trigger": {
                        "api_endpoint": f"{mock_url}/api/events/mock-event-vau/register",
                        "api_method": "POST",
                        "api_payload": {"sessionIds": ["slot-tier5-dual-alpha"]},
                        "success_status_codes": [200],
                    },
                    "enable_dual_trigger": True,
                }
                res = await client.post("/api/tasks", json=create_payload)
                assert res.status_code == 201, f"Task creation failed: {res.text}"
                task_data = res.json()
                task_id = task_data["id"]
                assert task_data["name"] == "Tier5 Dual-Trigger Master Sniper"
                assert task_data["credentials"]["password"] == "********", (
                    "SECURITY LEAK: Password must be masked in REST API response!"
                )

            # 4. Verify Encrypted Persistence on Disk
            assert enc_file.exists(), "tasks.enc.json must exist on disk"
            raw_bytes = enc_file.read_bytes()
            assert len(raw_bytes) > 0, "Ciphertext file is empty"
            assert b"UltraSecretP@ssword2026!" not in raw_bytes, (
                "SECURITY LEAK: Plaintext password found in encrypted file!"
            )
            assert b"Tier5 Dual-Trigger Master Sniper" not in raw_bytes, (
                "SECURITY LEAK: Plaintext task name found in encrypted file!"
            )

            # Storage decryption verification
            stored_task = storage.get_task(task_id)
            assert stored_task is not None
            assert stored_task.credentials.password == "UltraSecretP@ssword2026!"
            assert stored_task.status in (TaskStatus.PENDING, TaskStatus.STOPPED)

            # 5. Connect WebSocket clients for task logs and global status
            task_ws_url = f"{backend_handle.ws_url}/ws/tasks/{task_id}/logs"
            global_ws_url = f"{backend_handle.ws_url}/ws/logs"

            async with websockets.connect(task_ws_url) as task_ws, \
                       websockets.connect(global_ws_url) as global_ws:

                # 6. Start task via REST API
                async with httpx.AsyncClient(base_url=backend_url) as client:
                    start_res = await client.post(f"/api/tasks/{task_id}/start")
                    assert start_res.status_code == 200
                    assert start_res.json()["status"] == "started"

                # 7. Verify live logs and POLLING transition streamed over WebSocket
                received_events = []
                polling_started = False
                start_wait = time.time()

                while time.time() - start_wait < 5.0 and not polling_started:
                    try:
                        raw_msg = await asyncio.wait_for(task_ws.recv(), timeout=1.0)
                        msg = json.loads(raw_msg)
                        received_events.append(msg)
                        if msg.get("type") == "status_change" and msg.get("status") == "POLLING":
                            polling_started = True
                    except asyncio.TimeoutError:
                        pass

                assert polling_started, (
                    f"Task failed to reach POLLING state over WebSocket. Events: {received_events}"
                )

                # Verify mock portal is STILL full (no false trigger while full)
                async with httpx.AsyncClient(base_url=mock_url) as client:
                    mock_status = (await client.get("/mock/status")).json()
                    assert mock_status["is_full"] is True
                    assert mock_status["occupied_slots"] == 50

                # 8. Mock server dynamically frees 1 slot
                async with httpx.AsyncClient(base_url=mock_url) as client:
                    rel_res = await client.post("/mock/release-slot")
                    assert rel_res.status_code == 200
                    assert rel_res.json()["occupied_slots"] == 49

                # 9. Wait for SniperEngine to detect slot, execute dual-trigger, and stream SUCCESS
                success_status_received = False
                success_log_received = False
                global_success_received = False
                snipe_wait = time.time()

                while time.time() - snipe_wait < 7.0:
                    # Check task WebSocket
                    try:
                        raw_msg = await asyncio.wait_for(task_ws.recv(), timeout=0.3)
                        msg = json.loads(raw_msg)
                        received_events.append(msg)
                        if msg.get("type") == "status_change" and msg.get("status") == "SUCCESS":
                            success_status_received = True
                        if msg.get("type") == "log" and msg.get("level") == "SUCCESS":
                            success_log_received = True
                    except asyncio.TimeoutError:
                        pass

                    # Check global WebSocket (which triggers Web Audio arpeggio synth in dashboard)
                    try:
                        raw_gmsg = await asyncio.wait_for(global_ws.recv(), timeout=0.3)
                        gmsg = json.loads(raw_gmsg)
                        if gmsg.get("type") == "status_change" and gmsg.get("status") == "SUCCESS":
                            global_success_received = True
                        if gmsg.get("type") == "log" and gmsg.get("level") == "SUCCESS":
                            global_success_received = True
                    except asyncio.TimeoutError:
                        pass

                    if success_status_received and global_success_received:
                        break

                assert success_status_received, (
                    f"Expected SUCCESS status event over WebSocket. Received: {received_events}"
                )
                assert global_success_received, (
                    "Expected global SUCCESS event to trigger Web Audio API arpeggio in dashboard"
                )

                # 10. Allow brief storage write synchronization and verify Encrypted Storage on Disk
                await asyncio.sleep(0.5)
                updated_task = storage.get_task(task_id)
                assert updated_task is not None
                assert updated_task.status == TaskStatus.SUCCESS, (
                    f"Encrypted disk storage was not updated to SUCCESS: got {updated_task.status}"
                )

                # 11. Verify Mock Target Server registered the participant
                async with httpx.AsyncClient(base_url=mock_url) as client:
                    final_state = (await client.get("/mock/status")).json()
                    assert final_state["occupied_slots"] == 50
                    assert final_state["is_full"] is True
                    assert final_state["registered_participants_count"] >= 1
                    participant = final_state["registered_participants"][0]
                    assert "slot-tier5-dual-alpha" in participant["session_ids"]

        finally:
            # Clean shutdown of servers and engine
            backend_handle.stop()
            mock_handle.stop()
            try:
                engine.stop_all()
            except Exception:
                pass
            await engine.browser_manager.shutdown()


# ==============================================================================
# TEST 2: Storage Tamper Resistance & Auto-Recovery (Binary Noise & Truncation)
# ==============================================================================

@async_test
async def test_tier5_storage_tamper_resistance_and_recovery():
    """
    Tier 5 Tamper Resistance Test:
    1. Populate storage with an active task definition.
    2. Actively tamper with / corrupt the encrypted file on disk using binary noise.
    3. Verify REST API gracefully handles the corrupted file on GET /api/tasks:
       - Auto-recovers to empty state without throwing 500 or crashing server.
       - Backs up corrupted file to tasks.enc.json.corrupt.<timestamp>.
    4. Verify API can continue normal operations: create new task and persist cleanly.
    5. Corrupt file with a truncated Fernet token and verify GET /api/tasks/{id} returns 404 cleanly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        enc_file = tmp_path / "tasks.enc.json"
        key_file = tmp_path / "secret.key"

        app = create_app()
        storage = EncryptedStorage(filepath=enc_file, key_path=key_file)
        app.state.storage = storage

        backend_port = get_free_port()
        backend_handle = start_test_server(app=app, port=backend_port)
        backend_url = backend_handle.base_url

        try:
            async with httpx.AsyncClient(base_url=backend_url) as client:
                # 1. Create a valid task
                create_payload = {
                    "name": "Tamper Test Target Task",
                    "target_url": "http://127.0.0.1:8081/events/tamper",
                    "credentials": {"username": "admin", "password": "OriginalSecretKey#999"},
                }
                res = await client.post("/api/tasks", json=create_payload)
                assert res.status_code == 201
                orig_task_id = res.json()["id"]

                # Verify file exists on disk
                assert enc_file.exists()
                orig_size = enc_file.stat().st_size
                assert orig_size > 0

                # 2. MALICIOUS TAMPER ATTACK 1: Corrupt ciphertext with arbitrary garbage
                garbage_payload = b"\x00\xff\xfe\x01\x02MALICIOUS_CIPHERTEXT_NOISE_ATTACK_1337\xaa\xbb\xcc"
                enc_file.write_bytes(garbage_payload)

                # 3. Request task list via API -> MUST NOT crash (200 OK, empty list)
                list_res = await client.get("/api/tasks")
                assert list_res.status_code == 200, (
                    f"API crashed on corrupted ciphertext: {list_res.status_code} - {list_res.text}"
                )
                assert list_res.json() == [], "Corrupted file must safely auto-recover to empty list"

                # 4. Verify timestamped .corrupt backup file was generated on disk
                corrupt_backups = list(tmp_path.glob("tasks.enc.json.corrupt.*"))
                assert len(corrupt_backups) >= 1, "Failed to create .corrupt backup file"
                assert corrupt_backups[0].read_bytes() == garbage_payload

                # 5. Verify API remains fully operational: create a NEW task
                new_payload = {
                    "name": "Post-Recovery Resilient Task",
                    "target_url": "http://127.0.0.1:8081/events/recovered",
                    "credentials": {"username": "recovered_user", "password": "NewSecretKey#2026"},
                }
                post_res = await client.post("/api/tasks", json=new_payload)
                assert post_res.status_code == 201
                new_task_id = post_res.json()["id"]
                assert post_res.json()["name"] == "Post-Recovery Resilient Task"

                # Verify new task is persisted and readable
                get_res = await client.get(f"/api/tasks/{new_task_id}")
                assert get_res.status_code == 200
                assert get_res.json()["name"] == "Post-Recovery Resilient Task"

                # Sleep 1.1s so subsequent corruption produces distinct second-level timestamp
                await asyncio.sleep(1.1)

                # 6. MALICIOUS TAMPER ATTACK 2: Truncated Fernet token
                enc_file.write_bytes(b"gAAAAAB_TRUNCATED_TOKEN_WITHOUT_HMAC_OR_IV")
                get_corrupt_res = await client.get(f"/api/tasks/{new_task_id}")
                assert get_corrupt_res.status_code == 404, (
                    f"Expected 404 on corrupted storage, got {get_corrupt_res.status_code}"
                )

                # Verify second backup was created
                all_backups = list(tmp_path.glob("tasks.enc.json.corrupt.*"))
                assert len(all_backups) >= 2, f"Second corrupt backup missing: found {all_backups}"

        finally:
            backend_handle.stop()


# ==============================================================================
# TEST 3: Storage Tamper Resistance (Valid Ciphertext of Malformed JSON)
# ==============================================================================

@async_test
async def test_tier5_storage_tamper_malformed_json_recovery():
    """
    Tier 5 Subtle Tamper Test:
    Corrupt file by encrypting non-JSON arbitrary string using the authentic Fernet key.
    The file will pass decryption (valid Fernet token) but fail JSON deserialization.
    Verify that EncryptedStorage catches JSONDecodeError, creates .corrupt backup,
    and returns empty dictionary without unhandled server exception.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        enc_file = tmp_path / "tasks.enc.json"
        key_file = tmp_path / "secret.key"

        storage = EncryptedStorage(filepath=enc_file, key_path=key_file)
        fernet = Fernet(storage.key)

        # Encrypt non-JSON string
        non_json_plaintext = b"THIS_IS_VALID_CIPHERTEXT_BUT_MALFORMED_JSON_OBJECT_{{[unclosed"
        valid_ciphertext_bad_json = fernet.encrypt(non_json_plaintext)
        enc_file.write_bytes(valid_ciphertext_bad_json)

        # Attempt to load
        loaded = storage.load_all()
        assert loaded == {}, "Storage must safely recover to empty dictionary on JSONDecodeError"

        # Check backup created
        backups = list(tmp_path.glob("tasks.enc.json.corrupt.*"))
        assert len(backups) == 1, "Backup for malformed JSON must be created"
        assert backups[0].read_bytes() == valid_ciphertext_bad_json


# ==============================================================================
# TEST 4: Concurrent Multi-Task Stress & Session Isolation (3 Concurrent Tasks)
# ==============================================================================

@async_test
async def test_tier5_concurrent_multitask_stress_and_session_isolation():
    """
    Tier 5 Concurrency & Isolation Stress Test:
    1. Spawn 3 concurrent sniper tasks targeting 3 distinct mock endpoints:
       - Task Alpha -> /events/event-alpha
       - Task Beta  -> /events/event-beta
       - Task Gamma -> /events/event-gamma
    2. Inject distinct authentication cookies into each task's BrowserContext:
       - Context Alpha gets auth_token=token_alpha_111
       - Context Beta gets auth_token=token_beta_222
       - Context Gamma gets auth_token=token_gamma_333
    3. Run all 3 tasks concurrently in POLLING mode.
    4. Verify 100% cookie & session isolation: ZERO leakage across contexts.
    5. Dynamically release slots in staggered sequence:
       - Release Event Beta first -> Task Beta snipes to SUCCESS; Tasks Alpha & Gamma stay POLLING.
       - Release Event Gamma second -> Task Gamma snipes to SUCCESS; Task Alpha stays POLLING.
       - Release Event Alpha third -> Task Alpha snipes to SUCCESS.
    6. Verify all 3 tasks reached SUCCESS and the portal recorded registrations with the
       exact respective participant cookies and session IDs (zero crosstalk).
    """
    multi_server = MultiPortalServer(event_ids=["event-alpha", "event-beta", "event-gamma"])
    server_port = get_free_port()
    server_handle = start_test_server(app=multi_server.app, port=server_port)
    base_url = server_handle.base_url

    engine = SniperEngine()

    try:
        # 1. Define 3 tasks
        tasks = [
            TaskModel(
                id=f"stress-task-{eid}",
                name=f"Concurrent Sniper {eid.upper()}",
                target_url=f"{base_url}/events/{eid}",
                polling_interval=0.3,
                jitter_sec=0.05,
                wait_condition="domcontentloaded",
                headless=True,
                selectors=TaskSelectors(
                    slot_selector="#btn-register",
                    slot_keyword="Đăng ký tham gia",
                    full_keyword="Hết chỗ",
                    ui_slot_selector="#btn-register",
                    ui_submit_selector="#btn-confirm-register",
                ),
                api_trigger=ApiTriggerConfig(
                    api_endpoint=f"{base_url}/api/events/{eid}/register",
                    api_method="POST",
                    api_payload={"sessionIds": [f"{eid}-session"]},
                    success_status_codes=[200],
                ),
                enable_dual_trigger=True,
            )
            for eid in ["event-alpha", "event-beta", "event-gamma"]
        ]

        task_a, task_b, task_c = tasks

        # Track statuses and logs per task
        statuses: Dict[str, List[TaskStatus]] = {t.id: [] for t in tasks}
        logs: Dict[str, List[LogEntry]] = {t.id: [] for t in tasks}

        def make_status_cb(tid: str):
            async def cb(t_id: str, st: TaskStatus):
                statuses[tid].append(st)
            return cb

        def make_log_cb(tid: str):
            async def cb(entry: LogEntry):
                logs[tid].append(entry)
            return cb

        # 2. Start all 3 tasks concurrently
        for t in tasks:
            await engine.start_task(
                task=t,
                status_callback=make_status_cb(t.id),
                log_callback=make_log_cb(t.id),
            )

        # 3. Wait for all 3 tasks to reach POLLING state
        wait_start = time.time()
        while time.time() - wait_start < 4.0:
            if all(engine.get_task_status(t.id) == TaskStatus.POLLING for t in tasks):
                break
            await asyncio.sleep(0.1)

        for t in tasks:
            assert engine.get_task_status(t.id) == TaskStatus.POLLING, (
                f"Task {t.id} failed to enter POLLING state: {engine.get_task_status(t.id)}"
            )

        # 4. Verify Context & Cookie Isolation
        ctx_a = engine.browser_manager.get_context(task_a.id)
        ctx_b = engine.browser_manager.get_context(task_b.id)
        ctx_c = engine.browser_manager.get_context(task_c.id)

        assert ctx_a is not None and ctx_b is not None and ctx_c is not None
        assert len({id(ctx_a), id(ctx_b), id(ctx_c)}) == 3, "All 3 contexts must be distinct instances!"

        # Inject unique cookies into each context
        await ctx_a.add_cookies([{"name": "auth_token", "value": "token_alpha_111", "url": base_url}])
        await ctx_b.add_cookies([{"name": "auth_token", "value": "token_beta_222", "url": base_url}])
        await ctx_c.add_cookies([{"name": "auth_token", "value": "token_gamma_333", "url": base_url}])

        # Assert 0% cookie leakage across contexts
        cookies_a = await ctx_a.cookies(base_url)
        cookies_b = await ctx_b.cookies(base_url)
        cookies_c = await ctx_c.cookies(base_url)

        val_a = [c["value"] for c in cookies_a if c["name"] == "auth_token"]
        val_b = [c["value"] for c in cookies_b if c["name"] == "auth_token"]
        val_c = [c["value"] for c in cookies_c if c["name"] == "auth_token"]

        assert val_a == ["token_alpha_111"], f"Leakage in Context A: {cookies_a}"
        assert val_b == ["token_beta_222"], f"Leakage in Context B: {cookies_b}"
        assert val_c == ["token_gamma_333"], f"Leakage in Context C: {cookies_c}"

        # 5. Staggered Dynamic Slot Release
        async with httpx.AsyncClient(base_url=base_url) as client:
            # Step A: Release Event Beta first
            await client.post("/mock/release/event-beta")

            # Wait for Task Beta to snipe and reach SUCCESS
            beta_wait = time.time()
            while time.time() - beta_wait < 5.0:
                if engine.get_task_status(task_b.id) == TaskStatus.SUCCESS:
                    break
                await asyncio.sleep(0.15)

            assert engine.get_task_status(task_b.id) == TaskStatus.SUCCESS
            # Verify Tasks Alpha and Gamma REMAIN in POLLING (no false triggers)
            assert engine.get_task_status(task_a.id) == TaskStatus.POLLING, "Task Alpha triggered prematurely!"
            assert engine.get_task_status(task_c.id) == TaskStatus.POLLING, "Task Gamma triggered prematurely!"

            # Step B: Release Event Gamma second
            await client.post("/mock/release/event-gamma")

            gamma_wait = time.time()
            while time.time() - gamma_wait < 5.0:
                if engine.get_task_status(task_c.id) == TaskStatus.SUCCESS:
                    break
                await asyncio.sleep(0.15)

            assert engine.get_task_status(task_c.id) == TaskStatus.SUCCESS
            assert engine.get_task_status(task_a.id) == TaskStatus.POLLING, "Task Alpha triggered prematurely!"

            # Step C: Release Event Alpha third
            await client.post("/mock/release/event-alpha")

            alpha_wait = time.time()
            while time.time() - alpha_wait < 5.0:
                if engine.get_task_status(task_a.id) == TaskStatus.SUCCESS:
                    break
                await asyncio.sleep(0.15)

            assert engine.get_task_status(task_a.id) == TaskStatus.SUCCESS

        # 6. Verify Mock Server received distinct requests with proper cookies
        async with httpx.AsyncClient(base_url=base_url) as client:
            status_a = (await client.get("/mock/status/event-alpha")).json()
            status_b = (await client.get("/mock/status/event-beta")).json()
            status_c = (await client.get("/mock/status/event-gamma")).json()

            assert status_a["registered_participants_count"] == 1
            assert status_b["registered_participants_count"] == 1
            assert status_c["registered_participants_count"] == 1

            assert "event-alpha-session" in status_a["registered_participants"][0]["session_ids"]
            assert "event-beta-session" in status_b["registered_participants"][0]["session_ids"]
            assert "event-gamma-session" in status_c["registered_participants"][0]["session_ids"]

            # Verify authentic cookies reached server without crosstalk
            user_a = status_a["registered_participants"][0].get("user", {})
            user_b = status_b["registered_participants"][0].get("user", {})
            user_c = status_c["registered_participants"][0].get("user", {})

            cookie_header_a = user_a.get("cookie_header", "")
            cookie_header_b = user_b.get("cookie_header", "")
            cookie_header_c = user_c.get("cookie_header", "")

            assert "token_alpha_111" in cookie_header_a or user_a.get("cookies", {}).get("auth_token") == "token_alpha_111"
            assert "token_beta_222" in cookie_header_b or user_b.get("cookies", {}).get("auth_token") == "token_beta_222"
            assert "token_gamma_333" in cookie_header_c or user_c.get("cookies", {}).get("auth_token") == "token_gamma_333"

    finally:
        server_handle.stop()
        try:
            engine.stop_all()
        except Exception:
            pass
        await engine.browser_manager.shutdown()


# ==============================================================================
# TEST 5: Slot Contention Race with 3 Competing Tasks (API Trigger)
# ==============================================================================

@async_test
async def test_tier5_slot_contention_single_slot_three_competing_tasks():
    """
    Tier 5 Adversarial Race Condition Test:
    3 concurrent tasks competing simultaneously for a SINGLE slot released on the portal.
    Uses direct API trigger (matching test_adversarial_slot_contention_api_race) to strictly
    test atomic slot reservation.
    Verify:
    1. Exactly ONE task claims the slot and transitions to SUCCESS.
    2. The other TWO tasks receive 409 Conflict rejection from the portal and reach FAILED.
    3. The portal records exactly 1 registration (no double-claim vulnerability).
    """
    mock_port = get_free_port()
    mock_handle = start_mock_server(port=mock_port)
    base_url = f"http://127.0.0.1:{mock_port}"

    engine = SniperEngine()

    try:
        # Ensure portal starts full (50/50)
        async with httpx.AsyncClient(base_url=base_url) as client:
            await client.post("/mock/reset")

        competing_tasks = [
            TaskModel(
                id=f"contender-task-{i}",
                name=f"Contender Task #{i}",
                target_url=f"{base_url}/events/mock-event-vau",
                polling_interval=0.2,
                jitter_sec=0.01,
                wait_condition="domcontentloaded",
                headless=True,
                detection_type="DOM_SELECTOR",
                selectors=TaskSelectors(
                    slot_selector="#btn-register",
                    slot_keyword="Đăng ký tham gia",
                    full_keyword="Hết chỗ",
                ),
                api_trigger=ApiTriggerConfig(
                    api_endpoint=f"{base_url}/api/events/mock-event-vau/register",
                    api_method="POST",
                    api_payload={"sessionIds": [f"contender-{i}-slot"]},
                    success_status_codes=[200],
                ),
                enable_dual_trigger=False,
            )
            for i in range(1, 4)
        ]

        # Start all 3 competing tasks
        for t in competing_tasks:
            await engine.start_task(t)

        # Wait for all 3 to be POLLING
        wait_poll = time.time()
        while time.time() - wait_poll < 4.0:
            if all(engine.get_task_status(t.id) == TaskStatus.POLLING for t in competing_tasks):
                break
            await asyncio.sleep(0.1)

        # Release exactly ONE slot
        async with httpx.AsyncClient(base_url=base_url) as client:
            rel = await client.post("/mock/release-slot")
            assert rel.status_code == 200
            assert rel.json()["occupied_slots"] == 49

        # Wait up to 6.0 seconds for all tasks to reach terminal state (SUCCESS or FAILED)
        resolve_start = time.time()
        while time.time() - resolve_start < 6.0:
            statuses = [engine.get_task_status(t.id) for t in competing_tasks]
            if all(s in (TaskStatus.SUCCESS, TaskStatus.FAILED) for s in statuses):
                break
            await asyncio.sleep(0.2)

        final_statuses = [engine.get_task_status(t.id) for t in competing_tasks]
        success_count = sum(1 for s in final_statuses if s == TaskStatus.SUCCESS)
        failed_count = sum(1 for s in final_statuses if s == TaskStatus.FAILED)

        # Exactly 1 task must succeed and 2 must fail
        assert success_count == 1, (
            f"Expected exactly 1 winner in single-slot contention race, got {success_count}. Statuses: {final_statuses}"
        )
        assert failed_count == 2, (
            f"Expected exactly 2 losers (FAILED), got {failed_count}. Statuses: {final_statuses}"
        )

        # Portal state check: exactly 1 registered participant
        async with httpx.AsyncClient(base_url=base_url) as client:
            final_portal = (await client.get("/mock/status")).json()
            assert final_portal["registered_participants_count"] == 1, (
                f"Double-claim detected! Registered: {final_portal['registered_participants_count']}"
            )
            assert final_portal["occupied_slots"] == 50
            assert final_portal["is_full"] is True

    finally:
        mock_handle.stop()
        try:
            engine.stop_all()
        except Exception:
            pass
        await engine.browser_manager.shutdown()

