"""Dual-trigger registration executor.

Concurrently executes direct in-page API fetch via page.evaluate()
AND automated Playwright UI selector clicking using asyncio.gather.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from playwright.async_api import Page

from sniper.models import ApiTriggerConfig, TaskModel, TaskSelectors

logger = logging.getLogger("sniper.dual_trigger")


class DualTriggerResult(BaseModel):
    """Result of dual-trigger execution."""

    success: bool
    winner: str = "NONE"  # API | UI | BOTH | NONE
    api_result: Dict[str, Any] = Field(default_factory=dict)
    ui_result: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class DualTriggerExecutor:
    """Executes simultaneous in-context API fetch and UI selector interaction."""

    API_EVAL_JS = """
    async ([endpoint, method, headers, payload]) => {
        let targetUrl = endpoint;
        try {
            targetUrl = new URL(endpoint, window.location.origin).href;
        } catch (e) {
            targetUrl = endpoint;
        }

        const options = {
            method: (method || 'POST').toUpperCase(),
            headers: Object.assign({'Content-Type': 'application/json'}, headers || {}),
            credentials: 'include'
        };

        if (payload !== undefined && payload !== null && options.method !== 'GET' && options.method !== 'HEAD') {
            options.body = typeof payload === 'string' ? payload : JSON.stringify(payload);
        }

        try {
            const res = await fetch(targetUrl, options);
            let data = null;
            const text = await res.text();
            try {
                data = JSON.parse(text);
            } catch (err) {
                data = text;
            }
            return {
                ok: res.ok,
                status: res.status,
                statusText: res.statusText,
                data: data,
                error: null
            };
        } catch (err) {
            return {
                ok: false,
                status: 0,
                statusText: '',
                data: null,
                error: err ? (err.message || err.toString()) : 'Fetch error'
            };
        }
    }
    """

    @classmethod
    async def run_api_trigger(
        cls,
        page: Page,
        api_config: ApiTriggerConfig,
    ) -> Dict[str, Any]:
        """Execute direct in-page fetch() within the active browser session."""
        if not api_config.api_endpoint:
            return {
                "trigger": "API",
                "skipped": True,
                "ok": False,
                "reason": "No API endpoint configured",
            }

        try:
            logger.info("Triggering direct in-page API: %s", api_config.api_endpoint)
            raw_res = await page.evaluate(
                cls.API_EVAL_JS,
                [
                    api_config.api_endpoint,
                    api_config.api_method,
                    api_config.api_headers,
                    api_config.api_payload,
                ],
            )

            status = raw_res.get("status", 0)
            ok = raw_res.get("ok", False) or (status in api_config.success_status_codes)

            # Optional check for success keyword in payload
            if ok and api_config.success_keyword:
                content_str = str(raw_res.get("data", ""))
                if api_config.success_keyword not in content_str:
                    ok = False

            return {
                "trigger": "API",
                "skipped": False,
                "ok": ok,
                "status": status,
                "data": raw_res.get("data"),
                "error": raw_res.get("error"),
            }
        except Exception as e:
            logger.error("API trigger execution failed: %s", e)
            return {
                "trigger": "API",
                "skipped": False,
                "ok": False,
                "status": 0,
                "data": None,
                "error": str(e),
            }

    @classmethod
    async def run_ui_trigger(
        cls,
        page: Page,
        selectors: TaskSelectors,
    ) -> Dict[str, Any]:
        """Execute automated Playwright UI selector sequence."""
        # Check if any UI selector is configured
        has_ui = bool(
            selectors.ui_slot_selector
            or selectors.ui_submit_selector
            or selectors.slot_selector
        )
        if not has_ui:
            return {
                "trigger": "UI",
                "skipped": True,
                "ok": False,
                "reason": "No UI selectors configured",
            }

        actions_performed: List[str] = []
        try:
            # 0. If target_course_codes specified, click checkboxes in matching rows
            if selectors.target_course_codes:
                codes = [c.strip() for c in selectors.target_course_codes.split(",") if c.strip()]
                for code in codes:
                    try:
                        row = page.locator(f"tr:has-text('{code}')").first
                        if await row.count() > 0 and await row.is_visible():
                            target_btn = row.locator("input[type='checkbox']:not([disabled]), input[type='radio']:not([disabled]), button:not([disabled])").first
                            if await target_btn.count() > 0:
                                logger.info("UI trigger clicking course row item: %s", code)
                                await target_btn.click(timeout=2500)
                                actions_performed.append(f"clicked_course_row:{code}")
                    except Exception as e:
                        logger.debug("Failed clicking course row for %s: %s", code, e)

            # 1. Click slot selection / item radio / checkbox if configured
            slot_sel = selectors.ui_slot_selector or selectors.slot_selector
            if slot_sel and not actions_performed:
                logger.info("UI trigger clicking slot selector: %s", slot_sel)
                await page.click(slot_sel, timeout=2500)
                actions_performed.append(f"clicked_slot:{slot_sel}")

            # 2. Click submit button
            submit_sel = selectors.ui_submit_selector or selectors.submit_selector
            if submit_sel:
                logger.info("UI trigger clicking submit selector: %s", submit_sel)
                await page.click(submit_sel, timeout=2500)
                actions_performed.append(f"clicked_submit:{submit_sel}")

            # 3. Optional modal confirmation button (shorter timeout, ignore failure)
            if selectors.ui_confirm_modal_selector:
                try:
                    logger.info(
                        "UI trigger attempting confirm modal: %s",
                        selectors.ui_confirm_modal_selector,
                    )
                    await page.click(selectors.ui_confirm_modal_selector, timeout=1500)
                    actions_performed.append(
                        f"clicked_modal:{selectors.ui_confirm_modal_selector}"
                    )
                except Exception:
                    pass

            return {
                "trigger": "UI",
                "skipped": False,
                "ok": True,
                "actions": actions_performed,
                "error": None,
            }
        except Exception as e:
            logger.warning("UI trigger execution failed: %s", e)
            return {
                "trigger": "UI",
                "skipped": False,
                "ok": False,
                "actions": actions_performed,
                "error": str(e),
            }

    @classmethod
    async def execute(
        cls,
        page: Page,
        task: TaskModel,
    ) -> DualTriggerResult:
        """Concurrently execute API and UI registration triggers."""
        api_config = task.api_trigger
        selectors = task.selectors

        has_api = bool(api_config and api_config.api_endpoint)
        has_ui = bool(
            selectors
            and (
                selectors.ui_slot_selector
                or selectors.ui_submit_selector
                or selectors.slot_selector
            )
        )

        logger.info(
            "Executing dual-trigger for task %s (has_api=%s, has_ui=%s, dual_enabled=%s)",
            task.id,
            has_api,
            has_ui,
            task.enable_dual_trigger,
        )

        # Build coroutines
        if task.enable_dual_trigger and has_api and has_ui:
            api_task = cls.run_api_trigger(page, api_config)
            ui_task = cls.run_ui_trigger(page, selectors)
            results = await asyncio.gather(api_task, ui_task, return_exceptions=True)
            api_res = (
                results[0]
                if not isinstance(results[0], Exception)
                else {"trigger": "API", "ok": False, "error": str(results[0])}
            )
            ui_res = (
                results[1]
                if not isinstance(results[1], Exception)
                else {"trigger": "UI", "ok": False, "error": str(results[1])}
            )
        elif has_api:
            api_res = await cls.run_api_trigger(page, api_config)
            ui_res = {
                "trigger": "UI",
                "skipped": True,
                "ok": False,
                "reason": "Not configured or dual trigger disabled",
            }
        elif has_ui:
            api_res = {
                "trigger": "API",
                "skipped": True,
                "ok": False,
                "reason": "Not configured or dual trigger disabled",
            }
            ui_res = await cls.run_ui_trigger(page, selectors)
        else:
            api_res = {
                "trigger": "API",
                "skipped": True,
                "ok": False,
                "reason": "No triggers configured",
            }
            ui_res = {
                "trigger": "UI",
                "skipped": True,
                "ok": False,
                "reason": "No triggers configured",
            }

        api_ok = bool(isinstance(api_res, dict) and api_res.get("ok"))
        ui_ok = bool(isinstance(ui_res, dict) and ui_res.get("ok"))
        overall_success = api_ok or ui_ok

        winner = "NONE"
        if api_ok and ui_ok:
            winner = "BOTH"
        elif api_ok:
            winner = "API"
        elif ui_ok:
            winner = "UI"

        msg = f"Dual-trigger completed: success={overall_success}, winner={winner}"
        logger.info(msg)

        return DualTriggerResult(
            success=overall_success,
            winner=winner,
            api_result=api_res if isinstance(api_res, dict) else {"error": str(api_res)},
            ui_result=ui_res if isinstance(ui_res, dict) else {"error": str(ui_res)},
            message=msg,
        )


async def execute_dual_trigger(page: Page, task: TaskModel) -> DualTriggerResult:
    """Convenience helper to execute dual-trigger."""
    return await DualTriggerExecutor.execute(page, task)
