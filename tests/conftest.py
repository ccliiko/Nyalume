"""测试全局夹具：隔离临时数据库，并让浏览器用例复用同一个 Edge 进程。

旧实现里每条 UI 用例都自己 ``sync_playwright()`` + ``chromium.launch()`` +
``browser.close()``。Windows 上关闭一次 Edge 约 6.6 秒，test_chat_layout 的 56 条用例
光启动/关闭浏览器就要 5 分钟以上；关闭偶尔还会卡住，而 pytest-timeout 的线程计时器
打断不了 Playwright 的原生等待，于是卡住的那一轮会把当时正在跑的用例记成失败
（表现为随机一条 UI 用例在进度条上出现 F）。

这里改成：Playwright 与 Edge 每个测试模块只启动/关闭一次，每条用例只新建一个
浏览器上下文（localStorage、Cookie、路由拦截互不影响）。test_chat_layout 的
56 条用例因此只需要一次浏览器启停，耗时从 6 分半降到 40 秒左右。
"""

import os
import shutil
import tempfile

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="nyalume_test_")
os.environ["MEMORY_DB"] = os.path.join(_TMP_DIR, "test.db")
os.environ["AGENT_WORKSPACE"] = os.path.join(_TMP_DIR, "workspace")


@pytest.fixture(scope="module")
def playwright_engine():
    """每个测试模块只启动一次 Playwright。

    刻意不用 session 作用域：Playwright 同步 API 会在主线程留下一个处于
    running 状态的事件循环，只要它活着，后面任何 ``asyncio.run()`` 都会抛
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``。
    模块级既能复用浏览器，又能在模块结束时把这个循环收掉。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="module")
def browser(playwright_engine):
    """同一个测试模块内复用同一个 Edge 进程。"""
    browser = playwright_engine.chromium.launch(channel="msedge", headless=True)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture
def browser_context(browser):
    """每条用例一个独立浏览器上下文，保证用例之间不共享页面状态。"""
    context = browser.new_context(viewport={"width": 1100, "height": 760})
    try:
        yield context
    finally:
        context.close()


_SLOW_FIXTURES = frozenset({"chat_page", "browser_context", "browser"})


def pytest_collection_modifyitems(config, items):
    """用到真实浏览器的用例自动带上 slow 标记。

    这样 ``pytest -m "not slow"`` 只跑不依赖 Edge 的快速用例，
    不必在每个模块里手工维护标记，也不会漏掉新写的 UI 用例。
    """
    mark = pytest.mark.slow
    for item in items:
        if _SLOW_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(mark)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
