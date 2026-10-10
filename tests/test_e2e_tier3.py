"""
Tier 3: Cross-Feature Combination Test Suite
Tests interactions between isolated browser pools, multi-task concurrent executions,
relative URL resolution in in-page evaluate, and stop-all orchestrations.
"""

import asyncio
from functools import wraps
import pytest
import httpx

from tests.mock_target_server import start_mock_server, mock_state
from sniper.engine import SniperEngine
from sniper.dual_trigger import DualTriggerExecutor
from sniper.models import (
    ApiTriggerConfig,
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
def mock_server_tier3():
    """Start mock target server on port 8084 for Tier 3 tests."""
    handle = start_mock_server(host="127.0.0.1", port=8084)
    yield "http://127.0.0.1:8084"
    handle.stop()


@async_test
async def test_multi_task_concurrent_isolation_no_leakage(mock_server_tier3):
    """
    Verify multiple independent sniper tasks can run simultaneously
    without context, session, or callback leakage across tasks.
    """
    base_url = mock_server_tier3
    engine = SniperEngine()
    try:
        task1 = TaskModel(
            id="multi-task-1",
            name="Concurrent Task 1",
            target_url=f"{base_url}/events/mock-event-vau",
            polling_interval=1.0,
            headless=True,
        )
        task2 = TaskModel(
            id="multi-task-2",
            name="Concurrent Task 2",
            target_url=f"{base_url}/events/cmuy26oks0o3c2mo4k39w31v5",
            polling_interval=1.0,
            headless=True,
        )

        t1_statuses = []
        t2_statuses = []

        await engine.start_task(
            task1,
            status_callback=lambda tid, st: t1_statuses.append(st),
        )
        await engine.start_task(
            task2,
            status_callback=lambda tid, st: t2_statuses.append(st),
        )

        await asyncio.sleep(1.2)

        assert engine.get_task_status(task1.id) == TaskStatus.POLLING
        assert engine.get_task_status(task2.id) == TaskStatus.POLLING

        # Contexts must be distinct
        ctx1 = engine.browser_manager.get_context(task1.id)
        ctx2 = engine.browser_manager.get_context(task2.id)
        assert ctx1 is not None
        assert ctx2 is not None
        assert ctx1 != ctx2

        # Stopping Task 1 must not stop Task 2
        engine.stop_task(task1.id)
        await asyncio.sleep(0.3)
        assert engine.get_task_status(task1.id) == TaskStatus.STOPPED
        assert engine.get_task_status(task2.id) == TaskStatus.POLLING

        engine.stop_task(task2.id)
    finally:
        await engine.browser_manager.shutdown()


@async_test
async def test_dual_trigger_relative_url_resolution(mock_server_tier3):
    """
    Verify DualTriggerExecutor handles relative API URLs (e.g. '/api/events/...')
    resolving correctly against the browser window origin in page.evaluate().
    """
    base_url = mock_server_tier3
    async with httpx.AsyncClient(base_url=base_url) as client:
        await client.post("/mock/reset")
        await client.post("/mock/release-slot")

    engine = SniperEngine()
    try:
        ctx = await engine.browser_manager.create_context("test-relative-url", headless=True)
        page = await ctx.new_page()
        await page.goto(f"{base_url}/events/mock-event-vau", wait_until="domcontentloaded")

        # Relative endpoint without scheme/host
        task = TaskModel(
            id="relative-url-task",
            target_url=f"{base_url}/events/mock-event-vau",
            api_trigger=ApiTriggerConfig(
                api_endpoint="/api/events/mock-event-vau/register",
                api_method="POST",
                api_payload={"sessionIds": ["slot-relative-alpha"]},
                success_status_codes=[200],
            ),
            enable_dual_trigger=True,
        )

        result = await DualTriggerExecutor.execute(page, task)
        assert result.success is True
        assert result.winner in ("API", "BOTH")
        assert result.api_result.get("ok") is True
        assert result.api_result.get("status") == 200
    finally:
        await engine.browser_manager.shutdown()


@async_test
async def test_stop_all_tasks_orchestration(mock_server_tier3):
    """Verify stop_all() safely halts all running tasks across the engine."""
    base_url = mock_server_tier3
    engine = SniperEngine()
    try:
        tasks = [
            TaskModel(
                id=f"stop-all-task-{i}",
                target_url=f"{base_url}/events/mock-event-vau",
                polling_interval=2.0,
                headless=True,
            )
            for i in range(3)
        ]

        for t in tasks:
            await engine.start_task(t)

        await asyncio.sleep(1.0)
        for t in tasks:
            assert engine.get_task_status(t.id) == TaskStatus.POLLING

        engine.stop_all()
        await asyncio.sleep(0.5)

        for t in tasks:
            assert engine.get_task_status(t.id) == TaskStatus.STOPPED
    finally:
        await engine.browser_manager.shutdown()
