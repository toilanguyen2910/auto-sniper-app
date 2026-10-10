import asyncio
from functools import wraps
from playwright.async_api import async_playwright

from sniper.dual_trigger import DualTriggerExecutor
from sniper.engine import SniperEngine
from sniper.models import TaskModel, TaskSelectors


def async_test(f):
    """Decorator to run async test functions synchronously via asyncio.run."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@async_test
async def test_target_course_codes_row_filtering():
    """Verify that sniper checks and clicks only targeted course codes in a multi-course table."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <body>
        <table id="courseTable">
            <tr id="row-math">
                <td>MATH101</td>
                <td>Toán giải tích</td>
                <td>Hết chỗ</td>
                <td><input type="checkbox" disabled id="cb-math" /></td>
            </tr>
            <tr id="row-phys">
                <td>PHYS102</td>
                <td>Vật lý đại cương</td>
                <td>Còn chỗ</td>
                <td><input type="checkbox" id="cb-phys" /></td>
            </tr>
            <tr id="row-it">
                <td>IT002</td>
                <td>Lập trình Python</td>
                <td>Còn chỗ</td>
                <td><input type="checkbox" id="cb-it" /></td>
            </tr>
        </table>
        <button id="btnSave">Lưu đăng ký</button>
    </body>
    </html>
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html_content)

        # 1. Target only MATH101 (which is full)
        task_math_only = TaskModel(
            target_url="http://dummy",
            selectors=TaskSelectors(
                target_course_codes="MATH101",
                full_keyword="Hết chỗ",
                slot_keyword="Còn chỗ",
            ),
        )
        engine = SniperEngine()
        is_avail_math = await engine._check_slot_available(page, task_math_only)
        # Should be False because MATH101 is full
        assert is_avail_math is False

        # 2. Target IT002 (which is available)
        task_it = TaskModel(
            target_url="http://dummy",
            selectors=TaskSelectors(
                target_course_codes="MATH101, IT002",
                full_keyword="Hết chỗ",
                slot_keyword="Còn chỗ",
                ui_submit_selector="#btnSave",
            ),
        )
        is_avail_it = await engine._check_slot_available(page, task_it)
        assert is_avail_it is True

        # 3. Test UI trigger targeting IT002
        cb_it = page.locator("#cb-it")
        assert not await cb_it.is_checked()

        res = await DualTriggerExecutor.run_ui_trigger(page, task_it.selectors)
        assert res["ok"] is True
        assert any("clicked_course_row:IT002" in a for a in res["actions"])
        assert await cb_it.is_checked()

        # cb-phys should NOT have been touched
        assert not await page.locator("#cb-phys").is_checked()

        await browser.close()
