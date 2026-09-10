"""用隔离页面和模拟配置接口验证真实模型菜单脚本。"""

from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from nyalume.core import vision
from nyalume.frontends.web import server


def test_reasoning_selection_updates_chip_and_menu():
    source = (Path(__file__).resolve().parents[1] /
              "nyalume/frontends/web/static/index.html").read_text(encoding="utf-8")
    script = source[source.index("    const EFFORT_LABELS ="):source.index("    const modelMenu =")]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page()
            page.set_content('<button id="model-chip"></button><div id="model-menu"></div>')
            page.evaluate("""() => {
                window.config = {LLM_PROVIDER: 'custom', LLM_MODEL: 'test-model', LLM_REASONING_EFFORT: 'low'};
                window.api = async (url, options) => {
                    if (options) {
                        const payload = JSON.parse(options.body);
                        Object.assign(window.config, payload.values);
                        for (const key of payload.clear) delete window.config[key];
                        return {ok: true};
                    }
                    return {
                        providers: {custom: {name: '自定义兼容接口', models: [], vision_models: []}},
                        fields: Object.entries(window.config).map(([key, value]) => ({key, value}))
                    };
                };
            }""")
            page.add_script_tag(content='let modelCurrent = "";\n' + script)
            page.evaluate("refreshModelChip()")
            expect(page.get_by_role("button", name="低", exact=True)).to_have_class("current")
            for label, expected_chip in [("高", "test-model · 高"), ("最高", "test-model · 最高"), ("自动", "test-model")]:
                page.get_by_role("button", name=label, exact=True).click()
                expect(page.locator("#model-chip")).to_have_text(expected_chip)
                expect(page.get_by_role("button", name=label, exact=True)).to_have_class("current", timeout=2000)
                assert page.locator("#model-menu button.current").all_text_contents() == ["自定义 · test-model", label]
        finally:
            browser.close()


def test_vision_uses_the_current_provider(monkeypatch):
    for key in ("VISION_API_KEY", "VISION_BASE_URL", "VISION_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "real-key")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    assert vision._vision_settings()[2] == "deepseek-v4-flash-vision-exp"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-luna")
    assert vision._vision_settings()[1:] == ("https://api.openai.com/v1", "gpt-5.6-luna")


def test_config_infers_provider_and_limits_its_models(monkeypatch):
    monkeypatch.setattr(server, "_env_map", lambda: {
        "LLM_API_KEY": "secret",
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL": "gpt-5.6-luna",
    })
    fields = {item["key"]: item for item in server.get_config()["fields"]}
    assert fields["LLM_PROVIDER"]["value"] == "openai"
    assert fields["LLM_MODEL"]["options"] == server.PROVIDERS["openai"]["models"]
