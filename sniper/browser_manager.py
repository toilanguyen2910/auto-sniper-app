"""Browser manager providing dual-pool Playwright instances and isolated task contexts."""

import asyncio
import logging
from typing import Dict, List, Optional

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

logger = logging.getLogger("sniper.browser_manager")


class BrowserManager:
    """Manages dual Chromium browser instances (headless & headful)

    and isolates BrowserContext lifecycles per sniper task.
    """

    DEFAULT_LAUNCH_ARGS: List[str] = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]

    BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

    def __init__(self, launch_args: Optional[List[str]] = None) -> None:
        self._launch_args = (
            launch_args if launch_args is not None else list(self.DEFAULT_LAUNCH_ARGS)
        )
        self._playwright: Optional[Playwright] = None
        self._headless_browser: Optional[Browser] = None
        self._headful_browser: Optional[Browser] = None
        self._contexts: Dict[str, BrowserContext] = {}
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Return True if Playwright is actively running."""
        return self._playwright is not None

    async def _ensure_playwright(self) -> Playwright:
        """Ensure Playwright instance is started."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    async def get_browser(self, headless: bool = True) -> Browser:
        """Get or lazily launch the requested Chromium browser instance."""
        async with self._lock:
            pw = await self._ensure_playwright()
            if headless:
                if self._headless_browser is None or not self._headless_browser.is_connected():
                    logger.info("Launching headless Chromium pool...")
                    self._headless_browser = await pw.chromium.launch(
                        headless=True,
                        args=self._launch_args,
                    )
                return self._headless_browser
            else:
                if self._headful_browser is None or not self._headful_browser.is_connected():
                    logger.info("Launching headful Chromium pool...")
                    self._headful_browser = await pw.chromium.launch(
                        headless=False,
                        args=self._launch_args,
                    )
                return self._headful_browser

    async def create_context(
        self,
        task_id: str,
        headless: bool = True,
        block_assets: bool = True,
        viewport: Optional[dict] = None,
        user_agent: Optional[str] = None,
        **context_kwargs,
    ) -> BrowserContext:
        """Create a dedicated, isolated BrowserContext for a task.

        Ensures 100% cookie and session isolation. Optionally intercepts
        and aborts unnecessary image/media/font network requests to accelerate polling.
        """
        # If an existing context exists for this task, cleanly close it first
        if task_id in self._contexts:
            await self.close_context(task_id)

        browser = await self.get_browser(headless=headless)

        # Context options
        options = {
            "viewport": viewport or {"width": 1280, "height": 720},
            "accept_downloads": False,
        }
        if user_agent:
            options["user_agent"] = user_agent
        options.update(context_kwargs)

        context = await browser.new_context(**options)

        if block_assets:
            await self._apply_asset_blocking(context)

        self._contexts[task_id] = context
        logger.debug(
            "Created isolated context for task %s (headless=%s, block_assets=%s)",
            task_id,
            headless,
            block_assets,
        )
        return context

    async def _apply_asset_blocking(self, context: BrowserContext) -> None:
        """Route interception to abort non-essential assets during polling."""

        async def _block_handler(route):
            try:
                request = route.request
                if request.resource_type in self.BLOCKED_RESOURCE_TYPES:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                # Page or route might have already closed or been navigated away
                pass

        await context.route("**/*", _block_handler)

    def get_context(self, task_id: str) -> Optional[BrowserContext]:
        """Retrieve the active BrowserContext for a task if it exists."""
        return self._contexts.get(task_id)

    async def close_context(self, task_id: str) -> None:
        """Close and discard a task's BrowserContext and all its pages."""
        context = self._contexts.pop(task_id, None)
        if context is not None:
            try:
                await context.close()
                logger.debug("Closed context for task %s", task_id)
            except Exception as e:
                logger.warning("Error closing context for task %s: %s", task_id, e)

    async def shutdown(self) -> None:
        """Close all active task contexts and terminate both browser pools."""
        async with self._lock:
            # 1. Close all active contexts
            context_ids = list(self._contexts.keys())
            for task_id in context_ids:
                await self.close_context(task_id)
            self._contexts.clear()

            # 2. Close browser instances
            if self._headless_browser is not None:
                try:
                    await self._headless_browser.close()
                except Exception as e:
                    logger.warning("Error closing headless browser: %s", e)
                self._headless_browser = None

            if self._headful_browser is not None:
                try:
                    await self._headful_browser.close()
                except Exception as e:
                    logger.warning("Error closing headful browser: %s", e)
                self._headful_browser = None

            # 3. Stop Playwright
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning("Error stopping Playwright: %s", e)
                self._playwright = None

            logger.info("BrowserManager completely shut down.")

    async def __aenter__(self) -> "BrowserManager":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.shutdown()
