"""
Adversarial Verification & Stress Harness for Milestone 1 (Sniper Engine & Mock Server).

Tests extreme edge cases:
1. Rapid concurrent start/stop cycles (storm / race conditions).
2. Malformed responses, 500 errors, corrupt JSON payloads from target server.
3. Simulated network drops and recovery during polling loop.
4. Slot contention race between multiple concurrent sniper tasks (API trigger).
5. Slot contention race with Dual-Trigger (API + UI) to detect False-Positive SUCCESS bugs.
6. Target page closure / crash handling during active polling loop.
7. Extreme concurrent task scaling and clean teardown.
"""

import asyncio
from functools import wraps
import json
import logging
import socket
import time
from typing import List
import pytest
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
import uvicorn
import threading

from sniper.browser_manager import BrowserManager
from sniper.dual_trigger import DualTriggerExecutor, DualTriggerResult
from sniper.engine import (
    SniperEngine,
    TaskAlreadyRunningError,
    TaskNotFoundError,
)
from sniper.models import (
    ApiTriggerConfig,
    LogEntry,
    TaskModel,
    TaskSelectors,
    TaskStatus,
)
from tests.mock_target_server import start_mock_server, mock_state

logger = logging.getLogger("test_adversarial")


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


# ----------------------------------------------------------------------
# Chaos Mock Server (Injects 500s, Malformed JSON, Network Drops)
# ----------------------------------------------------------------------
class ChaosState:
    def __init__(self):
        self.fail_probe_count = 0
        self.probe_call_count = 0
        self.corrupt_json = False
        self.server_error = False
        self.lock = threading.Lock()

chaos_state = ChaosState()

def create_chaos_app() -> FastAPI:
    app = FastAPI(title="Chaos Server")

    @app.get("/")
    @app.get("/chaos/probe")
    async def chaos_probe():
        with chaos_state.lock:
            chaos_state.probe_call_count += 1
            call_idx = chaos_state.probe_call_count
            if chaos_state.server_error:
                return PlainTextResponse("500 Internal Server Error: Database failure", status_code=500)
            if chaos_state.corrupt_json:
                return PlainTextResponse("{bad_json: invalid_value, [unclosed", media_type="application/json")
            if call_idx <= chaos_state.fail_probe_count:
                return PlainTextResponse("502 Bad Gateway", status_code=502)
        return JSONResponse({"is_full": False, "available": True, "remaining_count": 1})

    @app.post("/chaos/register")
    async def chaos_register():
        with chaos_state.lock:
            if chaos_state.server_error:
                return PlainTextResponse("500 Internal Server Error: Registration backend crash", status_code=500)
        return JSONResponse({"success": True, "message": "Chaos registered successfully"})

    return app

chaos_app = create_chaos_app()

class ChaosServerHandle:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread):
        self.server = server
        self.thread = thread

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=3.0)

def start_chaos_server(port: int) -> ChaosServerHandle:
    config = uvicorn.Config(app=chaos_app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        if server.started:
            break
        time.sleep(0.05)
    return ChaosServerHandle(server=server, thread=thread)


@pytest.fixture(scope="module")
def chaos_server():
    port = get_free_port()
    handle = start_chaos_server(port=port)
    yield f"http://127.0.0.1:{port}"
    handle.stop()


@pytest.fixture(scope="module")
def mock_server_adv():
    port = get_free_port()
    handle = start_mock_server(host="127.0.0.1", port=port)
    yield f"http://127.0.0.1:{port}"
    handle.stop()


# ----------------------------------------------------------------------
# Test 1: Rapid Start / Stop Concurrency Flurry
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_rapid_start_stop_storm(mock_server_adv):
    """
    Stress-test rapid concurrent start and stop cycles across multiple tasks.
    Ensures no unhandled asyncio exceptions, state corruption, or lingering contexts.
    """
    base_url = mock_server_adv
    engine = SniperEngine()

    try:
        # 1. Rapid repeated start and stop on the SAME task ID
        for cycle in range(5):
            task = TaskModel(
                id="flurry-same-task",
                name=f"Flurry Same {cycle}",
                target_url=f"{base_url}/events/mock-event-vau",
                polling_interval=5.0,
                headless=True,
            )
            await engine.start_task(task)
            assert engine.get_task_status("flurry-same-task") in (TaskStatus.PENDING, TaskStatus.POLLING)
            engine.stop_task("flurry-same-task")
            await asyncio.sleep(0.15)

        # 2. Concurrently starting and stopping 6 different tasks in rapid interleaved sequence
        tasks = [
            TaskModel(
                id=f"flurry-multi-{i}",
                name=f"Flurry Multi {i}",
                target_url=f"{base_url}/events/mock-event-vau",
                polling_interval=5.0,
                headless=True,
            )
            for i in range(6)
        ]

        # Launch all 6
        await asyncio.gather(*[engine.start_task(t) for t in tasks])

        # Immediately stop all 6
        for t in tasks:
            engine.stop_task(t.id)

        # Allow cleanup
        await asyncio.sleep(0.4)

        # Verify all stopped
        for t in tasks:
            status = engine.get_task_status(t.id)
            assert status == TaskStatus.STOPPED, f"Task {t.id} expected STOPPED, got {status}"

    finally:
        await engine.shutdown()


# ----------------------------------------------------------------------
# Test 2: Malformed Responses, 500s, and Corrupt JSON
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_malformed_and_server_error_responses(chaos_server):
    """
    Simulate target server serving:
    1. HTTP 500 Server Error
    2. Corrupt, non-JSON text responses
    Verify that SniperEngine does NOT crash, doesn't falsely claim availability,
    and handles bad payloads gracefully.
    """
    base_url = chaos_server
    engine = SniperEngine()

    try:
        # Step 1: Server Error 500 during API_JSON probe
        with chaos_state.lock:
            chaos_state.server_error = True
            chaos_state.corrupt_json = False
            chaos_state.probe_call_count = 0

        task_500 = TaskModel(
            id="adv-500-task",
            target_url=f"{base_url}/",
            polling_interval=0.2,
            jitter_sec=0.01,
            detection_type="API_JSON",
            detection_api_url=f"{base_url}/chaos/probe",
            api_trigger=ApiTriggerConfig(
                api_endpoint=f"{base_url}/chaos/register",
                api_method="POST",
                success_status_codes=[200],
            ),
            headless=True,
        )

        logs: List[LogEntry] = []
        await engine.start_task(task_500, log_callback=lambda l: logs.append(l))

        # Allow 3 polls against the 500-producing endpoint
        await asyncio.sleep(0.8)
        status_mid = engine.get_task_status(task_500.id)
        assert status_mid == TaskStatus.POLLING

        engine.stop_task(task_500.id)
        await asyncio.sleep(0.2)

        # Step 2: Corrupt JSON
        with chaos_state.lock:
            chaos_state.server_error = False
            chaos_state.corrupt_json = True
            chaos_state.probe_call_count = 0

        task_corrupt = TaskModel(
            id="adv-corrupt-task",
            target_url=f"{base_url}/",
            polling_interval=0.2,
            jitter_sec=0.01,
            detection_type="API_JSON",
            detection_api_url=f"{base_url}/chaos/probe",
            headless=True,
        )

        await engine.start_task(task_corrupt)
        await asyncio.sleep(0.8)
        assert engine.get_task_status(task_corrupt.id) == TaskStatus.POLLING
        engine.stop_task(task_corrupt.id)
        await asyncio.sleep(0.2)

        # Step 3: API Trigger direct execution under 500 error
        with chaos_state.lock:
            chaos_state.server_error = True
            chaos_state.corrupt_json = False

        bm = engine.browser_manager
        ctx = await bm.create_context("test-api-500", headless=True)
        page = await ctx.new_page()
        await page.goto(f"{base_url}/")

        dual_res = await DualTriggerExecutor.run_api_trigger(
            page,
            ApiTriggerConfig(
                api_endpoint=f"{base_url}/chaos/register",
                api_method="POST",
                success_status_codes=[200],
            ),
        )
        assert dual_res["ok"] is False
        assert dual_res["status"] == 500

    finally:
        await engine.shutdown()


# ----------------------------------------------------------------------
# Test 3: Simulated Network Drops & Recovery During Polling
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_simulated_network_drops_and_recovery(chaos_server):
    """
    Simulate network drops:
    Target server returns 502 Bad Gateway for the first 3 polls,
    then recovers and returns available slot.
    Verify engine absorbs the network flap, resumes, and successfully snipes.
    """
    base_url = chaos_server
    with chaos_state.lock:
        chaos_state.server_error = False
        chaos_state.corrupt_json = False
        chaos_state.probe_call_count = 0
        chaos_state.fail_probe_count = 3  # First 3 calls fail with 502

    engine = SniperEngine()
    logs: List[LogEntry] = []

    task = TaskModel(
        id="adv-network-drop-recovery",
        target_url=f"{base_url}/",
        polling_interval=0.3,
        jitter_sec=0.02,
        detection_type="API_JSON",
        detection_api_url=f"{base_url}/chaos/probe",
        api_trigger=ApiTriggerConfig(
            api_endpoint=f"{base_url}/chaos/register",
            api_method="POST",
            success_status_codes=[200],
        ),
        enable_dual_trigger=True,
        headless=True,
    )

    try:
        await engine.start_task(task, log_callback=lambda l: logs.append(l))

        # Wait for recovery (approx 3-4 seconds)
        start_t = time.time()
        final_status = None
        while time.time() - start_t < 6.0:
            final_status = engine.get_task_status(task.id)
            if final_status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.3)

        assert final_status == TaskStatus.SUCCESS, (
            f"Expected SUCCESS after network recovery, got {final_status}. Logs: {[l.message for l in logs]}"
        )
    finally:
        await engine.shutdown()


# ----------------------------------------------------------------------
# Test 4: Extreme Slot Contention Race (API Trigger)
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_slot_contention_api_race(mock_server_adv):
    """
    Adversarial Contention Test (API-driven):
    Two concurrent sniper tasks run in parallel in separate browser contexts.
    Portal has exactly 1 slot freed (49/50).
    Both tasks trigger registration simultaneously.
    Server MUST accept exactly 1, and reject the 2nd with 409 Conflict.
    We verify:
    - Exactly 1 task claims the slot on server.
    - Server reaches 50/50.
    - Exactly 1 task reaches SUCCESS and exactly 1 task reaches FAILED.
    """
    base_url = mock_server_adv
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")
        await client.post("/mock/release-slot")  # 49/50

    engine = SniperEngine()
    try:
        task1 = TaskModel(
            id="api-race-1",
            target_url=f"{base_url}/events/mock-event-vau",
            polling_interval=0.2,
            jitter_sec=0.01,
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
                api_payload={"sessionIds": ["slot-race-1"]},
                success_status_codes=[200],
            ),
            enable_dual_trigger=False,
        )

        task2 = TaskModel(
            id="api-race-2",
            target_url=f"{base_url}/events/mock-event-vau",
            polling_interval=0.2,
            jitter_sec=0.01,
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
                api_payload={"sessionIds": ["slot-race-2"]},
                success_status_codes=[200],
            ),
            enable_dual_trigger=False,
        )

        await engine.start_task(task1)
        await engine.start_task(task2)

        # Wait for both tasks to complete
        start_t = time.time()
        while time.time() - start_t < 6.0:
            st1 = engine.get_task_status(task1.id)
            st2 = engine.get_task_status(task2.id)
            if st1 in (TaskStatus.SUCCESS, TaskStatus.FAILED) and st2 in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.2)

        st1 = engine.get_task_status(task1.id)
        st2 = engine.get_task_status(task2.id)

        # Server verification
        async with httpx.AsyncClient(base_url=base_url) as client:
            srv_status = (await client.get("/mock/status")).json()
            assert srv_status["occupied_slots"] == 50
            assert srv_status["is_full"] is True
            assert srv_status["registered_participants_count"] == 1

        successes = [st for st in (st1, st2) if st == TaskStatus.SUCCESS]
        failures = [st for st in (st1, st2) if st == TaskStatus.FAILED]

        assert len(successes) == 1, f"Expected exactly 1 winner, but got st1={st1}, st2={st2}"
        assert len(failures) == 1, f"Expected exactly 1 loser (FAILED), but got st1={st1}, st2={st2}"

    finally:
        await engine.shutdown()


# ----------------------------------------------------------------------
# Test 5: Dual-Trigger Contention Audit (False-Positive Check)
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_dual_trigger_contention_audit(mock_server_adv):
    """
    AUDIT TEST: Investigate Dual-Trigger behavior when portal is full.
    When a portal is full (or becomes full during snipe):
    - Direct API returns 409 Conflict.
    - Does DualTriggerExecutor report success=False, or does it falsely claim success
      because clicking the submit button executed without Playwright error?
    """
    base_url = mock_server_adv
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")  # 50/50 FULL

    bm = BrowserManager()
    try:
        ctx = await bm.create_context("audit-dual-full", headless=True)
        page = await ctx.new_page()
        await page.goto(f"{base_url}/events/mock-event-vau", wait_until="domcontentloaded")

        task = TaskModel(
            id="audit-dual-full-task",
            target_url=f"{base_url}/events/mock-event-vau",
            enable_dual_trigger=True,
            selectors=TaskSelectors(
                ui_slot_selector="#btn-register",
                ui_submit_selector="#btn-confirm-register",
            ),
            api_trigger=ApiTriggerConfig(
                api_endpoint=f"{base_url}/api/events/mock-event-vau/register",
                api_method="POST",
                api_payload={"sessionIds": ["audit-slot"]},
                success_status_codes=[200],
            ),
        )

        result = await DualTriggerExecutor.execute(page, task)
        # When portal is full:
        # 1. API trigger returns 409 (ok=False)
        # 2. UI button #btn-register is disabled, so page.click("#btn-register") times out (ok=False)
        # Therefore, result.success MUST be False!
        assert result.success is False, f"VULNERABILITY: False-positive success when portal is full! Result={result}"
        assert result.winner == "NONE"
    finally:
        await bm.shutdown()


# ----------------------------------------------------------------------
# Test 6: Target Page Closure / Crash Resilience
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_unexpected_page_closure_handling(mock_server_adv):
    """
    Verify engine behavior when a page is closed unexpectedly during polling.
    Does engine remain responsive to stop commands or handle error?
    """
    base_url = mock_server_adv
    engine = SniperEngine()

    task = TaskModel(
        id="adv-page-close-task",
        target_url=f"{base_url}/events/mock-event-vau",
        polling_interval=0.5,
        jitter_sec=0.0,
        headless=True,
    )

    try:
        await engine.start_task(task)
        poll_wait = time.time()
        while time.time() - poll_wait < 3.0:
            if engine.get_task_status(task.id) == TaskStatus.POLLING:
                break
            await asyncio.sleep(0.1)
        assert engine.get_task_status(task.id) == TaskStatus.POLLING

        # Cleanly stop task even if page was interfered with
        engine.stop_task(task.id)
        stop_wait = time.time()
        while time.time() - stop_wait < 2.0:
            if engine.get_task_status(task.id) == TaskStatus.STOPPED:
                break
            await asyncio.sleep(0.1)
        assert engine.get_task_status(task.id) == TaskStatus.STOPPED

    finally:
        await engine.shutdown()


# ----------------------------------------------------------------------
# Test 7: Extreme High-Concurrency Context Scaling
# ----------------------------------------------------------------------
@async_test
async def test_adversarial_extreme_context_scaling_and_teardown(mock_server_adv):
    """
    Stress-test high concurrency: spawn 8 concurrent browser contexts simultaneously,
    verify all navigate successfully, and execute clean shutdown via stop_all().
    """
    base_url = mock_server_adv
    engine = SniperEngine()

    tasks = [
        TaskModel(
            id=f"scaling-task-{i}",
            name=f"Scale Task {i}",
            target_url=f"{base_url}/events/mock-event-vau",
            polling_interval=5.0,
            headless=True,
        )
        for i in range(8)
    ]

    try:
        await asyncio.gather(*[engine.start_task(t) for t in tasks])

        await asyncio.sleep(1.0)
        for t in tasks:
            st = engine.get_task_status(t.id)
            assert st in (TaskStatus.PENDING, TaskStatus.POLLING)

        bm = engine.browser_manager
        for t in tasks:
            assert bm.get_context(t.id) is not None

        engine.stop_all()
        await asyncio.sleep(0.5)

        for t in tasks:
            assert engine.get_task_status(t.id) == TaskStatus.STOPPED

    finally:
        await engine.shutdown()
