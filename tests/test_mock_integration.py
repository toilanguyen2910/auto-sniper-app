"""
Mock Server Integration Test Suite
Verifies complete end-to-end autonomous slot detection, dual-trigger execution,
and task lifecycle transition to SUCCESS without human intervention.
"""

import asyncio
from functools import wraps
import time
import pytest
import httpx

from tests.mock_target_server import start_mock_server, mock_state
from sniper.engine import SniperEngine
from sniper.models import (
    ApiTriggerConfig,
    LogEntry,
    TaskModel,
    TaskSelectors,
    TaskStatus,
)


def async_test(f):
    """Decorator to run async test functions synchronously via asyncio.run."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@pytest.fixture(scope="module")
def mock_server_port8081():
    """Start mock target server on port 8081 for integration test."""
    handle = start_mock_server(host="127.0.0.1", port=8081)
    yield "http://127.0.0.1:8081"
    handle.stop()


@async_test
async def test_end_to_end_sniper_integration_against_mock_portal(mock_server_port8081):
    """
    Core Integration Test:
    1. Connect to mock portal (port 8081) starting in full state (50/50).
    2. Start SniperEngine task with fast polling (0.5s).
    3. Verify task polls repeatedly while portal is full.
    4. Dynamically release 1 slot via POST /mock/release-slot.
    5. Verify engine detects slot, triggers dual-trigger registration, claims the slot.
    6. Verify mock server records registration.
    7. Verify task reaches SUCCESS status autonomously without human intervention.
    """
    base_url = mock_server_port8081

    # 1. Ensure mock server starts in clean 50/50 full state
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")
        init_status = (await client.get("/mock/status")).json()
        assert init_status["occupied_slots"] == 50
        assert init_status["is_full"] is True
        assert init_status["registered_participants_count"] == 0

    # 2. Configure sniper task targeting the mock portal
    task = TaskModel(
        id="integration-sniper-vau",
        name="Mock VAU Sniper Integration",
        target_url=f"{base_url}/events/mock-event-vau",
        polling_interval=0.4,
        jitter_sec=0.05,
        wait_condition="domcontentloaded",
        block_media_assets=True,
        headless=True,
        manual_login=False,
        detection_type="DOM_SELECTOR",
        selectors=TaskSelectors(
            slot_selector="#btn-register",
            slot_keyword="Đăng ký tham gia",
            full_keyword="Hết chỗ",
            ui_slot_selector="#btn-register",
            ui_submit_selector="#btn-confirm-register",
        ),
        api_trigger=ApiTriggerConfig(
            api_endpoint=f"{base_url}/api/events/mock-event-vau/register",
            api_method="POST",
            api_payload={"sessionIds": ["slot-alpha"]},
            success_status_codes=[200],
        ),
        enable_dual_trigger=True,
    )

    engine = SniperEngine()
    logs_received = []
    status_transitions = []

    async def log_callback(entry: LogEntry):
        logs_received.append(entry)

    async def status_callback(task_id: str, status: TaskStatus):
        status_transitions.append(status)

    try:
        # 3. Start task
        await engine.start_task(
            task=task,
            log_callback=log_callback,
            status_callback=status_callback,
        )

        # 4. Wait for task to enter POLLING state and poll while full
        for _ in range(40):
            if engine.get_task_status(task.id) == TaskStatus.POLLING:
                break
            await asyncio.sleep(0.15)
        current_status = engine.get_task_status(task.id)
        assert current_status == TaskStatus.POLLING
        assert TaskStatus.POLLING in status_transitions

        # Verify mock server is STILL full (no false triggers)
        async with httpx.AsyncClient(base_url=base_url) as client:
            status_mid = (await client.get("/mock/status")).json()
            assert status_mid["occupied_slots"] == 50
            assert status_mid["is_full"] is True

        # 5. Dynamically release 1 slot
        async with httpx.AsyncClient(base_url=base_url) as client:
            rel_res = await client.post("/mock/release-slot")
            assert rel_res.status_code == 200
            assert rel_res.json()["occupied_slots"] == 49

        # 6. Wait for SniperEngine to detect slot and execute dual-trigger
        # Allow up to 6 seconds for poll cycle and snipe
        start_wait = time.time()
        final_status = None
        while time.time() - start_wait < 6.0:
            final_status = engine.get_task_status(task.id)
            if final_status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.2)

        # 7. Assertions: Task reached SUCCESS
        assert final_status == TaskStatus.SUCCESS, (
            f"Expected SUCCESS, got {final_status}. Logs: {[l.message for l in logs_received]}"
        )
        assert TaskStatus.SUCCESS in status_transitions

        # 8. Assertions: Mock server state reflects claimed slot
        async with httpx.AsyncClient(base_url=base_url) as client:
            final_server_state = (await client.get("/mock/status")).json()
            assert final_server_state["occupied_slots"] == 50
            assert final_server_state["is_full"] is True
            assert final_server_state["registered_participants_count"] >= 1
            assert final_server_state["successful_method"] in ("api", "ui", "dual")

            # Check registered participant details
            participant = final_server_state["registered_participants"][0]
            assert "slot-alpha" in participant["session_ids"]

        # 9. Verify structured logs contain key operational events
        log_messages = [l.message for l in logs_received]
        assert any("registered and initialized" in m.lower() for m in log_messages)
        assert any("starting polling loop" in m.lower() for m in log_messages)
        assert any("target slot detected" in m.lower() for m in log_messages)
        assert any("success" in l.level.lower() for l in logs_received)

    finally:
        try:
            engine.stop_task(task.id)
        except Exception:
            pass
        await engine.browser_manager.shutdown()


@async_test
async def test_sniper_auto_release_timer_integration(mock_server_port8081):
    """
    Verify integration when mock server is configured with auto_release_after timer.
    Sniper starts against full portal and detects opening automatically when timer expires.
    """
    base_url = mock_server_port8081

    # Reset with auto_release_after = 1.0 second
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")
        await client.post("/mock/configure", json={"auto_release_after": 1.0})

    task = TaskModel(
        id="auto-release-timer-task",
        name="Auto Release Timer Sniper",
        target_url=f"{base_url}/events/mock-event-vau",
        polling_interval=0.3,
        jitter_sec=0.05,
        wait_condition="domcontentloaded",
        headless=True,
        detection_type="DOM_SELECTOR",
        selectors=TaskSelectors(
            slot_selector="#btn-register",
            slot_keyword="Đăng ký tham gia",
            full_keyword="Hết chỗ",
            ui_slot_selector="#btn-register",
            ui_submit_selector="#btn-confirm-register",
        ),
        api_trigger=ApiTriggerConfig(
            api_endpoint=f"{base_url}/api/events/mock-event-vau/register",
            api_method="POST",
            api_payload={"sessionIds": ["slot-alpha"]},
            success_status_codes=[200],
        ),
        enable_dual_trigger=True,
    )

    engine = SniperEngine()
    try:
        await engine.start_task(task)

        # Wait up to 5 seconds for automatic slot release and snipe
        start_wait = time.time()
        final_status = None
        while time.time() - start_wait < 5.0:
            final_status = engine.get_task_status(task.id)
            if final_status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.2)

        assert final_status == TaskStatus.SUCCESS
    finally:
        try:
            engine.stop_task(task.id)
        except Exception:
            pass
        await engine.browser_manager.shutdown()
