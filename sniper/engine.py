"""Core Sniper Engine managing multi-task Playwright lifecycles,

polling loops, manual login authentication, and dual-trigger execution.
"""

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Dict, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from sniper.browser_manager import BrowserManager
from sniper.dual_trigger import execute_dual_trigger
from sniper.models import LogEntry, TaskModel, TaskStatus

logger = logging.getLogger("sniper.engine")


class SniperError(Exception):
    """Base exception for Sniper Engine."""


class TaskNotFoundError(SniperError):
    """Raised when an operation targets a non-existent task."""


class TaskAlreadyRunningError(SniperError):
    """Raised when attempting to start a task that is already running."""


class AwaitableBool:
    """Helper enabling dual synchronous and asynchronous evaluation of boolean results."""

    def __init__(self, value: bool) -> None:
        self.value = bool(value)

    def __bool__(self) -> bool:
        return self.value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, AwaitableBool):
            return self.value == other.value
        return self.value == other

    def __repr__(self) -> str:
        return f"AwaitableBool({self.value})"

    def __await__(self):
        async def _ret():
            return self.value

        return _ret().__await__()


class AwaitableNone:
    """Helper enabling dual synchronous and asynchronous evaluation for void methods."""

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: Any) -> bool:
        return other is None

    def __repr__(self) -> str:
        return "AwaitableNone()"

    def __await__(self):
        async def _ret():
            return None

        return _ret().__await__()


class SniperEngine:
    """Asynchronous multi-task sniper automation engine."""

    def __init__(self, browser_manager: Optional[BrowserManager] = None) -> None:
        self._browser_manager = browser_manager or BrowserManager()
        self._active_tasks: Dict[str, Dict[str, Any]] = {}
        self._last_known_status: Dict[str, TaskStatus] = {}
        self._lock = asyncio.Lock()

    @property
    def browser_manager(self) -> BrowserManager:
        """Return the underlying BrowserManager instance."""
        return self._browser_manager

    def get_task_status(self, task_id: str) -> TaskStatus:
        """Get the current or last known status of a task."""
        if task_id in self._active_tasks:
            return self._active_tasks[task_id]["status"]
        if task_id in self._last_known_status:
            return self._last_known_status[task_id]
        raise TaskNotFoundError(f"Task {task_id} not found")

    async def _emit_log(
        self,
        task_id: str,
        level: str,
        message: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit structured log to callback and standard logger."""
        entry = LogEntry(
            task_id=task_id,
            level=level,
            message=message,
            metrics=metrics,
        )
        logger.info("[%s][%s] %s", task_id, level, message)

        runtime = self._active_tasks.get(task_id)
        if runtime and runtime.get("log_callback"):
            callback = runtime["log_callback"]
            try:
                res = callback(entry)
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
            except Exception as e:
                logger.warning("Error in log_callback for task %s: %s", task_id, e)

    async def _set_status(self, task_id: str, status: TaskStatus) -> None:
        """Update and broadcast status transition."""
        self._last_known_status[task_id] = status
        runtime = self._active_tasks.get(task_id)
        if runtime:
            runtime["status"] = status
            callback = runtime.get("status_callback")
            if callback:
                try:
                    res = callback(task_id, status)
                    if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                        await res
                except Exception as e:
                    logger.warning("Error in status_callback for task %s: %s", task_id, e)

    async def start_task(
        self,
        task: TaskModel,
        log_callback: Optional[Callable[[LogEntry], Awaitable[None]]] = None,
        status_callback: Optional[Callable[[str, TaskStatus], Awaitable[None]]] = None,
    ) -> None:
        """Start a sniper task in a dedicated background coroutine."""
        async with self._lock:
            if task.id in self._active_tasks:
                existing = self._active_tasks[task.id]
                existing_task = existing.get("asyncio_task")
                if existing_task and not existing_task.done():
                    raise TaskAlreadyRunningError(
                        f"Task {task.id} is already running"
                    )

            stop_event = asyncio.Event()
            login_event = asyncio.Event()

            runtime_entry: Dict[str, Any] = {
                "task": task,
                "status": TaskStatus.PENDING,
                "asyncio_task": None,
                "stop_event": stop_event,
                "login_event": login_event,
                "log_callback": log_callback,
                "status_callback": status_callback,
            }
            self._active_tasks[task.id] = runtime_entry
            self._last_known_status[task.id] = TaskStatus.PENDING

        await self._set_status(task.id, TaskStatus.PENDING)
        await self._emit_log(task.id, "INFO", f"Task '{task.name}' registered and initialized.")

        # Launch runner task in background
        coro = self._run_task_lifecycle(task, runtime_entry)
        async_task = asyncio.create_task(coro)
        runtime_entry["asyncio_task"] = async_task

    def confirm_login(self, task_id: str) -> bool:
        """Signal that manual login or Captcha has been completed."""
        if task_id not in self._active_tasks:
            return False

        runtime = self._active_tasks[task_id]
        if runtime["status"] == TaskStatus.WAITING_LOGIN:
            runtime["login_event"].set()
            logger.info("Login confirmed for task %s", task_id)
            return True

        return False

    def stop_task(self, task_id: str) -> AwaitableNone:
        """Signal an active task to stop and cancel its execution."""
        if task_id not in self._active_tasks:
            raise TaskNotFoundError(f"Task {task_id} not found")

        runtime = self._active_tasks[task_id]
        runtime["stop_event"].set()
        async_task = runtime.get("asyncio_task")
        if async_task and not async_task.done():
            async_task.cancel()

        self._last_known_status[task_id] = TaskStatus.STOPPED
        runtime["status"] = TaskStatus.STOPPED
        return AwaitableNone()

    def stop_all(self) -> AwaitableNone:
        """Signal all active tasks to stop."""
        active_ids = list(self._active_tasks.keys())
        for task_id in active_ids:
            try:
                self.stop_task(task_id)
            except Exception:
                pass
        return AwaitableNone()

    async def _handle_manual_login(
        self,
        page: Page,
        task: TaskModel,
        runtime: Dict[str, Any],
    ) -> bool:
        """Manage WAITING_LOGIN state, awaiting user input or DOM/URL navigation."""
        await self._set_status(task.id, TaskStatus.WAITING_LOGIN)
        await self._emit_log(
            task.id,
            "INFO",
            "Visible browser window active. Please log in and solve any Captchas.",
        )

        login_event = runtime["login_event"]
        stop_event = runtime["stop_event"]
        waiters = [asyncio.create_task(login_event.wait())]

        # Post-login URL pattern auto-detection
        if task.selectors and task.selectors.post_login_url_pattern:
            async def _wait_url():
                try:
                    await page.wait_for_url(
                        task.selectors.post_login_url_pattern,
                        timeout=task.login_timeout_sec * 1000,
                    )
                    return True
                except Exception:
                    return False
            waiters.append(asyncio.create_task(_wait_url()))

        # Post-login selector auto-detection
        if task.selectors and task.selectors.post_login_selector:
            async def _wait_selector():
                try:
                    await page.wait_for_selector(
                        task.selectors.post_login_selector,
                        timeout=task.login_timeout_sec * 1000,
                    )
                    return True
                except Exception:
                    return False
            waiters.append(asyncio.create_task(_wait_selector()))

        # Stop event waiter
        stop_waiter = asyncio.create_task(stop_event.wait())
        waiters.append(stop_waiter)

        try:
            done, pending = await asyncio.wait(
                waiters,
                timeout=task.login_timeout_sec,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cleanup pending listeners
            for p in pending:
                p.cancel()

            if stop_event.is_set():
                return False

            if not done:
                await self._emit_log(task.id, "ERROR", "Login timed out.")
                return False

            await self._emit_log(
                task.id,
                "INFO",
                "Authentication verified! Handing over to high-speed polling.",
            )
            return True
        except asyncio.CancelledError:
            for w in waiters:
                w.cancel()
            raise

    async def _check_slot_available(self, page: Page, task: TaskModel) -> bool:
        """Check if target slot is available based on detection strategy."""
        detection_type = task.detection_type or "DOM_SELECTOR"

        if detection_type == "DOM_SELECTOR":
            selectors = task.selectors
            # 1. Check positive slot selector
            slot_sel = selectors.slot_selector or selectors.ui_slot_selector
            if slot_sel:
                try:
                    locator = page.locator(slot_sel).first
                    count = await locator.count()
                    if count > 0:
                        # Check visibility
                        if not await locator.is_visible():
                            return False

                        # Check if disabled
                        if await locator.is_disabled():
                            return False

                        # Check text against full_keyword
                        text = (await locator.inner_text()).strip()
                        if selectors.full_keyword and selectors.full_keyword.lower() in text.lower():
                            return False

                        if selectors.slot_keyword and selectors.slot_keyword.lower() not in text.lower():
                            return False

                        return True
                    else:
                        return False
                except Exception:
                    return False

            # 2. Check slot keyword in body
            if selectors.slot_keyword:
                try:
                    loc = page.locator(f"text={selectors.slot_keyword}").first
                    return await loc.is_visible()
                except Exception:
                    return False

            # 3. Check negative full keyword (slot available if full_keyword is absent)
            if selectors.full_keyword:
                try:
                    content = await page.content()
                    return selectors.full_keyword.lower() not in content.lower()
                except Exception:
                    return False

            return False

        elif detection_type == "KEYWORD":
            selectors = task.selectors
            try:
                content = await page.content()
                content_lower = content.lower()

                if selectors.slot_keyword:
                    has_slot = selectors.slot_keyword.lower() in content_lower
                    if selectors.full_keyword:
                        has_full = selectors.full_keyword.lower() in content_lower
                        return has_slot and not has_full
                    return has_slot

                if selectors.full_keyword:
                    return selectors.full_keyword.lower() not in content_lower

                return False
            except Exception:
                return False

        elif detection_type == "API_JSON":
            api_url = task.detection_api_url or (
                task.api_trigger.api_endpoint if task.api_trigger else None
            )
            if not api_url:
                return False

            try:
                probe_res = await page.evaluate(
                    """
                    async (url) => {
                        try {
                            const res = await fetch(url, { credentials: 'include' });
                            return await res.json();
                        } catch(e) {
                            return null;
                        }
                    }
                    """,
                    api_url,
                )
                if isinstance(probe_res, dict):
                    # Check common slot availability indicators
                    if probe_res.get("available") is True:
                        return True
                    if probe_res.get("is_full") is False:
                        return True
                    if int(probe_res.get("remaining_count", 0)) > 0:
                        return True
                    if int(probe_res.get("slots", 0)) > 0:
                        return True
                return False
            except Exception:
                return False

        return False

    async def _run_task_lifecycle(
        self,
        task: TaskModel,
        runtime: Dict[str, Any],
    ) -> None:
        """Orchestrate the complete lifecycle for an individual sniper task."""
        stop_event = runtime["stop_event"]
        page: Optional[Page] = None
        context = None

        try:
            # 1. Create isolated BrowserContext
            context = await self._browser_manager.create_context(
                task_id=task.id,
                headless=task.headless,
                block_assets=task.block_media_assets,
            )
            page = await context.new_page()

            # 2. Initial navigation
            await self._emit_log(task.id, "INFO", f"Navigating to {task.target_url}...")
            await page.goto(
                task.target_url,
                wait_until=task.wait_condition,
                timeout=30000,
            )

            # 3. Handle manual authentication / Captcha if required
            if task.manual_login:
                login_ok = await self._handle_manual_login(page, task, runtime)
                if not login_ok:
                    if stop_event.is_set():
                        await self._set_status(task.id, TaskStatus.STOPPED)
                        await self._emit_log(task.id, "WARNING", "Task stopped by operator.")
                    else:
                        await self._set_status(task.id, TaskStatus.FAILED)
                        await self._emit_log(task.id, "ERROR", "Login verification failed.")
                    return

            # 4. Polling loop
            await self._set_status(task.id, TaskStatus.POLLING)
            await self._emit_log(
                task.id,
                "INFO",
                f"Starting polling loop (interval={task.polling_interval}s, jitter=±{task.jitter_sec}s, wait={task.wait_condition})",
            )

            poll_count = 0
            slot_detected = False
            consecutive_errors = 0

            while not stop_event.is_set():
                poll_count += 1
                try:
                    slot_detected = await self._check_slot_available(page, task)
                    consecutive_errors = 0
                except PlaywrightTimeoutError:
                    consecutive_errors += 1
                    await self._emit_log(
                        task.id,
                        "WARNING",
                        f"Timeout checking slot on poll #{poll_count} (consecutive: {consecutive_errors})",
                    )
                except Exception as e:
                    consecutive_errors += 1
                    await self._emit_log(
                        task.id,
                        "WARNING",
                        f"Poll #{poll_count} check error: {e}",
                    )

                if slot_detected:
                    await self._emit_log(
                        task.id,
                        "CRITICAL",
                        f"TARGET SLOT DETECTED on poll #{poll_count}! Initiating dual-trigger snipe...",
                        metrics={"poll_count": poll_count},
                    )
                    break

                # Jittered sleep
                jitter = random.uniform(-task.jitter_sec, task.jitter_sec)
                delay = max(0.1, task.polling_interval + jitter)

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    # If stop_event was signaled
                    break
                except asyncio.TimeoutError:
                    pass

                if stop_event.is_set():
                    break

                # Reload page
                try:
                    await page.reload(wait_until=task.wait_condition, timeout=15000)
                except Exception as e:
                    logger.debug("Page reload warning on poll #%s: %s", poll_count, e)

            if stop_event.is_set():
                await self._set_status(task.id, TaskStatus.STOPPED)
                await self._emit_log(task.id, "WARNING", "Task stopped by user.")
                return

            if not slot_detected:
                await self._set_status(task.id, TaskStatus.FAILED)
                await self._emit_log(task.id, "ERROR", "Polling ended without slot detection.")
                return

            # 5. Sniping Phase
            await self._set_status(task.id, TaskStatus.SNIPING)
            trigger_result = await execute_dual_trigger(page, task)

            if trigger_result.success:
                await self._set_status(task.id, TaskStatus.SUCCESS)
                await self._emit_log(
                    task.id,
                    "SUCCESS",
                    f"SNIPE SUCCESSFUL! Winner: {trigger_result.winner}. {trigger_result.message}",
                    metrics={
                        "winner": trigger_result.winner,
                        "api_result": trigger_result.api_result,
                        "ui_result": trigger_result.ui_result,
                    },
                )
            else:
                await self._set_status(task.id, TaskStatus.FAILED)
                await self._emit_log(
                    task.id,
                    "ERROR",
                    f"Snipe failed: {trigger_result.message}",
                    metrics={
                        "api_result": trigger_result.api_result,
                        "ui_result": trigger_result.ui_result,
                    },
                )

        except asyncio.CancelledError:
            await self._set_status(task.id, TaskStatus.STOPPED)
            await self._emit_log(task.id, "WARNING", "Task coroutine cancelled.")
        except Exception as e:
            logger.exception("Unhandled error in task lifecycle for %s: %s", task.id, e)
            await self._set_status(task.id, TaskStatus.FAILED)
            await self._emit_log(task.id, "ERROR", f"Task encountered fatal error: {e}")
        finally:
            # 6. Teardown context and page
            await self._browser_manager.close_context(task.id)
            async with self._lock:
                self._active_tasks.pop(task.id, None)

    async def shutdown(self) -> None:
        """Cancel all running tasks and shut down browser manager."""
        self.stop_all()
        # Wait briefly for active tasks to conclude cancellation
        await asyncio.sleep(0.1)
        await self._browser_manager.shutdown()
        logger.info("SniperEngine completely shut down.")
