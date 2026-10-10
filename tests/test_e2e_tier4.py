"""
Tier 4: Real-World Application Scenario Test Suite
Simulates authentic end-to-end user workflows:
1. VAU Events preset registration workflow (canh_dang_ky_vauverse.py scenario).
2. Human-in-the-loop manual login & Captcha solving protocol (WAITING_LOGIN state).
3. Long polling stability with asset abortion.
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
def mock_server_tier4():
    """Start mock target server on port 8085 for Tier 4 tests."""
    handle = start_mock_server(host="127.0.0.1", port=8085)
    yield "http://127.0.0.1:8085"
    handle.stop()


@async_test
async def test_real_world_vauverse_preset_sniper(mock_server_tier4):
    """
    Simulates authentic VAUVERSE registration workflow as defined in
    canh_dang_ky_vauverse.py and auto-slot-sniper skill:
    - Event: cmuy26oks0o3c2mo4k39w31v5
    - Session: cmuy26okv0o3d2mo4fyiepdc1 (Buổi 1)
    - Full capacity detection -> Dynamic release -> Dual-trigger snipe -> SUCCESS.
    """
    base_url = mock_server_tier4
    event_id = "cmuy26oks0o3c2mo4k39w31v5"
    session_id = "cmuy26okv0o3d2mo4fyiepdc1"

    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")

    task = TaskModel(
        id="vauverse-real-task",
        name="VAUVERSE 2026 Registration Sniper",
        target_url=f"{base_url}/events/{event_id}",
        polling_interval=0.35,
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
            api_endpoint=f"/api/events/{event_id}/register",
            api_method="POST",
            api_payload={"sessionIds": [session_id]},
            success_status_codes=[200],
        ),
        enable_dual_trigger=True,
    )

    engine = SniperEngine()
    logs = []
    try:
        await engine.start_task(task, log_callback=lambda l: logs.append(l))

        # Let it poll while full
        await asyncio.sleep(1.0)
        assert engine.get_task_status(task.id) == TaskStatus.POLLING

        # Dynamically free slot
        async with httpx.AsyncClient(base_url=base_url) as client:
            await client.post("/mock/release-slot")

        # Wait for snipe
        start_wait = time.time()
        final_status = None
        while time.time() - start_wait < 5.0:
            final_status = engine.get_task_status(task.id)
            if final_status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.2)

        assert final_status == TaskStatus.SUCCESS

        # Verify registration registered on mock server
        async with httpx.AsyncClient(base_url=base_url) as client:
            state = (await client.get("/mock/status")).json()
            assert state["is_full"] is True
            assert state["occupied_slots"] == 50
            assert any(session_id in p["session_ids"] for p in state["registered_participants"])
    finally:
        try:
            engine.stop_task(task.id)
        except Exception:
            pass
        await engine.browser_manager.shutdown()


@async_test
async def test_real_world_manual_login_protocol_flow(mock_server_tier4):
    """
    Simulates human-in-the-loop manual login / Captcha protocol:
    1. Task configured with manual_login=True.
    2. Enters WAITING_LOGIN state, awaiting human action.
    3. User/system calls confirm_login(task_id).
    4. Task transitions from WAITING_LOGIN -> POLLING.
    5. After release, snipes slot and transitions to SUCCESS.
    """
    base_url = mock_server_tier4
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")

    task = TaskModel(
        id="manual-login-task",
        name="Manual Login & Captcha Sniper",
        target_url=f"{base_url}/events/mock-event-vau",
        polling_interval=0.3,
        jitter_sec=0.05,
        manual_login=True,
        login_timeout_sec=10,
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
            api_payload={"sessionIds": ["slot-manual-alpha"]},
            success_status_codes=[200],
        ),
        enable_dual_trigger=True,
    )

    engine = SniperEngine()
    try:
        await engine.start_task(task)

        # Wait up to 5s to enter WAITING_LOGIN (allowing Chromium launch time)
        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            if engine.get_task_status(task.id) == TaskStatus.WAITING_LOGIN:
                break
            await asyncio.sleep(0.2)
        assert engine.get_task_status(task.id) == TaskStatus.WAITING_LOGIN

        # Confirm login
        confirmed = engine.confirm_login(task.id)
        assert bool(confirmed) is True

        # Wait up to 5s for task to transition to POLLING
        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            if engine.get_task_status(task.id) == TaskStatus.POLLING:
                break
            await asyncio.sleep(0.2)
        assert engine.get_task_status(task.id) == TaskStatus.POLLING

        # Now release slot
        async with httpx.AsyncClient(base_url=base_url) as client:
            await client.post("/mock/release-slot")

        # Wait for snipe
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


@async_test
async def test_long_polling_stability_with_asset_blocking(mock_server_tier4):
    """
    Verify stability across multiple fast reload polling cycles
    with image/media/font route abortion enabled.
    """
    base_url = mock_server_tier4
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")

    task = TaskModel(
        id="long-poll-stability-task",
        name="Long Polling Stability",
        target_url=f"{base_url}/events/mock-event-vau",
        polling_interval=0.25,
        jitter_sec=0.02,
        wait_condition="domcontentloaded",
        block_media_assets=True,
        headless=True,
    )

    engine = SniperEngine()
    logs = []
    try:
        await engine.start_task(task, log_callback=lambda l: logs.append(l))

        # Let it poll for 2 seconds (around 6-8 poll cycles)
        await asyncio.sleep(2.0)

        # Must still be smoothly POLLING without crash
        assert engine.get_task_status(task.id) == TaskStatus.POLLING

        # Stop cleanly
        engine.stop_task(task.id)
        await asyncio.sleep(0.3)
        assert engine.get_task_status(task.id) == TaskStatus.STOPPED
    finally:
        await engine.browser_manager.shutdown()
