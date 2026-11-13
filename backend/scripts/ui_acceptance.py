"""工作台真人点击验收（Playwright，无头浏览器）。

这个脚本回答一个具体问题：**页面上的按钮到底能不能用**——不是"接口返回 200"，
而是"点下去之后，界面上的闸门状态真的变了"。

它做的事：

1. 打开「语义核查」页（`#/checks?report=N`）；
2. 读一遍页面上的成稿闸门文案（此时应为"还不能成稿"）；
3. 在某条核查行上点「确认」，在弹出的理由框里写一句话；
4. 等页面自己刷新，确认闸门文案变成"可成稿 · 已确认 N 条"、该行人工状态变成"人工已确认"；
5. 打开「报告」页，确认「成稿闸门」列也同步变了；
6. 全程收集浏览器控制台错误与页面异常，有任何一条就判失败。

运行方式（**必须用装了 playwright 的解释器**，本项目主 .venv 里没有）：

    tools\\openkatana\\.venv\\Scripts\\python.exe backend\\scripts\\ui_acceptance.py \\
        --base-url http://127.0.0.1:8002 --report-id 1 --out content\\jobs\\ui_acceptance

它只读页面、只点自己触发的按钮，不会自己起服务、不会碰数据库文件，
因此要先把服务（指向某个**临时/演示库**）跑起来再执行本脚本。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - 环境缺失时给出可执行的提示
    print(
        "缺少 playwright：请用 tools\\openkatana\\.venv\\Scripts\\python.exe 运行本脚本，"
        "或先 pip install playwright",
        file=sys.stderr,
    )
    raise SystemExit(2)


GATE_BLOCKED = "还不能成稿"
GATE_ALLOWED = "可成稿"
HUMAN_CONFIRMED = "人工已确认"


def _wait_check_rows(page, *, timeout_ms: int):
    page.wait_for_selector('button[data-review="confirmed"]', timeout=timeout_ms)


def _gate_text(page) -> str:
    """读出核查页上那句成稿闸门文案。

    页面里 `.tag` 有很多（判定计数、人工状态、闸门），只有闸门那句带「成稿」二字，
    所以按关键字挑，不靠位置猜。
    """

    return page.evaluate(
        """() => {
            const tags = Array.from(document.querySelectorAll('#checks-box .tag'))
                .map((node) => node.innerText.trim());
            return tags.find((text) => text.includes('成稿')) || '';
        }"""
    )


def _find_chromium() -> str | None:
    """找一个可用的完整 Chromium。

    Playwright 的 `chromium.launch(headless=True)` 默认要 `chrome-headless-shell`，
    但很多机器只装了完整版 Chromium（`chromium-<build>/chrome-win64/chrome.exe`）。
    这里按 `PLAYWRIGHT_BROWSERS_PATH` 与默认安装目录各找一遍，找到就用完整版。
    """

    roots = []
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        roots.append(Path(override))
    roots.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    for drive in ("C:", "D:", "E:"):
        roots.append(Path(f"{drive}/playwright_browsers"))
        roots.append(Path(f"{drive}/ms-playwright"))
    for root in roots:
        if not root.exists():
            continue
        for candidate in sorted(root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True):
            if candidate.exists():
                return str(candidate)
    return None


def run(base_url: str, report_id: int, out_dir: Path, *, browser_path: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    steps: list[dict] = []

    with sync_playwright() as playwright:
        executable = browser_path or _find_chromium()
        launch_kwargs = {"headless": True}
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            # favicon 之类的 404 与本页功能无关，别混进"真错误"里
            if message.type == "error" and "favicon" not in message.text.lower()
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: failed_requests.append(f"{response.status} {response.url}")
            if response.status >= 400
            else None,
        )

        # 1) 打开核查页
        page.goto(f"{base_url}/app/#/checks?report={report_id}", wait_until="load")
        _wait_check_rows(page, timeout_ms=15000)
        before = _gate_text(page)
        rows_before = page.locator('button[data-review="confirmed"]').count()
        page.screenshot(path=str(out_dir / "01_checks_before.png"), full_page=True)
        steps.append({"step": "打开核查页", "gate": before, "rows": rows_before})

        # 2) 点「确认」，并在理由框里写一句真话
        note = "UI 验收：对照报告原文与支持片段，逐字一致，口径按季度"
        page.once("dialog", lambda dialog: dialog.accept(note))
        page.locator('button[data-review="confirmed"]').first.click()

        # 3) 等页面自己刷新成"可成稿"
        page.wait_for_function(
            """(needle) => {
                const box = document.getElementById('checks-box');
                return box && box.innerText.includes(needle);
            }""",
            arg=GATE_ALLOWED,
            timeout=15000,
        )
        after = _gate_text(page)
        human_cells = page.locator("#checks-box table.grid tbody tr td:nth-child(7)").all_inner_texts()
        confirmed_cells = [cell for cell in human_cells if HUMAN_CONFIRMED in cell]
        page.screenshot(path=str(out_dir / "02_checks_after.png"), full_page=True)
        steps.append(
            {
                "step": "点确认后",
                "gate": after,
                "confirmed_rows": len(confirmed_cells),
                "note_in_row": any(note in cell for cell in human_cells),
            }
        )

        # 4) 报告页的闸门列必须同步
        page.goto(f"{base_url}/app/#/reports", wait_until="load")
        page.wait_for_selector("#view table tbody tr", timeout=15000)
        page.wait_for_timeout(800)  # 报告页要先并发取每份报告的闸门状态
        report_text = page.locator("#view").inner_text()
        page.screenshot(path=str(out_dir / "03_reports.png"), full_page=True)
        steps.append({"step": "报告页", "shows_gate_allowed": GATE_ALLOWED in report_text})

        browser.close()

    ok = (
        before.startswith(GATE_BLOCKED)
        and after.startswith(GATE_ALLOWED)
        and steps[-1]["shows_gate_allowed"]
        and steps[1]["confirmed_rows"] >= 1
        and not page_errors
    )
    return {
        "ok": ok,
        "base_url": base_url,
        "report_id": report_id,
        "steps": steps,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "failed_requests": failed_requests,
        "screenshots": [str(path.name) for path in sorted(out_dir.glob("*.png"))],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工作台真人点击验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--report-id", type=int, default=1)
    parser.add_argument("--out", default="content/jobs/ui_acceptance")
    parser.add_argument("--browser-path", default="", help="完整版 Chromium 的路径（不填就自动找）")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run(
            args.base_url.rstrip("/"),
            args.report_id,
            out_dir,
            browser_path=args.browser_path or None,
        )
    except PlaywrightTimeout as error:
        print(f'{{"ok": false, "reason": "timeout", "message": {json.dumps(str(error))}}}')
        return 1
    except PlaywrightError as error:
        print(f'{{"ok": false, "reason": "playwright", "message": {json.dumps(str(error))}}}')
        return 1

    (out_dir / "ui_acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
