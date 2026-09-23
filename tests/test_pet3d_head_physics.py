"""真实 Ammo 回归：视线姿态与碰撞解算同步，切舞不延迟开关物理。"""
from pathlib import Path
from urllib.parse import quote

import pytest
from playwright.sync_api import sync_playwright

from nyalume.frontends.pet.pet3d import model_profile, pet3d_win as pet

ROOT = Path(__file__).resolve().parents[1]


def test_head_pose_reaches_physics_in_same_frame(tmp_path, monkeypatch):
    model_path = ROOT / "models/锁瞑"
    if not model_path.exists():
        pytest.skip("本地模型未安装")
    monkeypatch.setattr(model_profile, "PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(pet, "_log", lambda *_: None)
    model_dir, pmx = pet._resolve_model(str(model_path))
    api = pet._NativeApi(enabled=False)
    api.configure_model(str(Path(model_dir) / pmx))
    port = pet.start_server(model_dir, str(ROOT / "motions"), api=api)
    source = (ROOT / "nyalume/frontends/pet/pet3d/viewer.html").read_text(encoding="utf-8")
    # 仅测试页面暴露模块内部状态；固定步进，避免计时与 GPU 速度影响断言。
    probe = """
    window.rig = {
      get helper() { return helper; }, get model() { return model; },
      nearEnd() { mainAction.time = mainClip.duration - END_FADE; },
      step(x, y) {
        lookTx = x; lookTy = y;
        clock.getDelta = () => { clock.elapsedTime += 1 / 60; return 1 / 60; };
        tickFrame();
      }
    };
    """
    source = source.replace("</script>\n</body>", probe + "</script>\n</body>")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("window.requestAnimationFrame = () => 0")
        page.route("**/*", lambda route: route.fulfill(body=source, content_type="text/html")
                   if route.request.resource_type == "document" else route.continue_())
        try:
            page.goto(f"http://127.0.0.1:{port}/viewer.html?pmx=/model/{quote(pmx)}&box=440x660&physics=1")
            page.wait_for_function("document.querySelector('#tip').textContent === ''", polling=100, timeout=60000)
            page.evaluate("""() => {
              const physics = rig.helper.objects.get(rig.model).physics;
              const update = physics.update.bind(physics);
              const reset = physics.reset.bind(physics);
              window.resets = 0;
              physics.reset = () => { resets++; return reset(); };
              window.samples = [];
              physics.update = dt => {
                const bones = rig.model.skeleton.bones.filter(b => ['頭', '首'].includes(b.name));
                const before = bones.map(b => b.quaternion.clone());
                const worldBefore = bones.map(b => b.matrixWorld.clone());
                update(dt);
                samples.push({bones, before, worldBefore});
              };
              window.checkFrames = () => {
                let mismatch = 0;
                for (let i = 0; i < 60; i++) {
                  rig.step(i < 30 ? 0.2 : -0.2, 0.17);
                  const {bones, before, worldBefore} = samples.at(-1);
                  bones.forEach((b, j) => {
                    mismatch = Math.max(mismatch, b.quaternion.clone().normalize().angleTo(before[j].normalize()));
                    // 不只是局部旋转：传给碰撞体的世界矩阵也必须是本帧的。
                    worldBefore[j].elements.forEach((value, k) => {
                      mismatch = Math.max(mismatch, Math.abs(value - b.matrixWorld.elements[k]));
                    });
                  });
                }
                return mismatch;
              };
            }""")
            assert page.evaluate("checkFrames()") < 1e-6
            dance = next((ROOT / "motions").rglob("IRIS OUT.vmd"))
            url = "/vmd/" + quote(dance.relative_to(ROOT / "motions").as_posix())
            assert page.evaluate("url => window.__setMotion(url)", url)
            assert page.evaluate("rig.helper.enabled.physics")
            assert page.evaluate("checkFrames()") < 1e-6
            assert page.evaluate("resets") == 1
            page.evaluate("rig.nearEnd()")
            assert page.evaluate("checkFrames()") < 1e-6
            assert page.evaluate("resets") == 1  # 自然收尾保持物理连续，不能重置。
            # 快速返回待机，旧动作不得遗留近一秒后的 reset/warmup 定时器。
            assert page.evaluate("window.__setMotion('')")
            assert page.evaluate("rig.helper.enabled.physics")
            assert page.evaluate("checkFrames()") < 1e-6
            assert page.evaluate("resets") == 2
            page.screenshot(path=str(tmp_path / "head-physics.png"))
            assert not errors
        finally:
            browser.close()
