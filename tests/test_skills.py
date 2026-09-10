"""Lightweight Skill installation and safety checks."""

import io
import zipfile

from nyalume.core import agent, skills, tools


def _isolated_skills(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    monkeypatch.setattr(skills, "SKILLS_DIR", str(root))
    monkeypatch.setattr(skills, "STATE_PATH", str(root / ".state.json"))
    return root


def test_install_enable_permissions_and_prompt(monkeypatch, tmp_path):
    _isolated_skills(monkeypatch, tmp_path)
    text = b"""---
name: study-helper
description: Helps students plan reviews.
version: 1.2
permissions: [web_search, file_read]
---
# Study Helper
Always split revision into small checkpoints.
"""

    installed = skills.install_bytes(text, "SKILL.md")
    assert installed["name"] == "study-helper"
    assert installed["enabled"] is True
    assert [item["label"] for item in installed["permissions"]] == ["联网", "读取文件"]
    assert "small checkpoints" in agent._system_prompt()

    skills.set_enabled(installed["id"], False)
    assert "small checkpoints" not in agent._system_prompt()


def test_zip_resources_stay_inside_skill_directory(monkeypatch, tmp_path):
    root = _isolated_skills(monkeypatch, tmp_path)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("demo/SKILL.md", "---\nname: demo\n---\nRead references/tips.md")
        archive.writestr("demo/references/tips.md", "Take a break every hour.")
        archive.writestr("../escape.txt", "nope")

    installed = skills.install_bytes(payload.getvalue(), "demo.zip")
    assert skills.read_resource(installed["id"], "references/tips.md") == "Take a break every hour."
    assert not (tmp_path / "escape.txt").exists()


def test_online_skill_install_always_requires_approval():
    need, message = tools.approval_needed(
        "skill_install_url", {"url": "https://example.com/study.zip"}
    )
    assert need is True
    assert "每次都要确认" in message
    assert tools.approval_rememberable("skill_install_url") is False
    names = {item["function"]["name"] for item in tools.TOOL_SCHEMAS}
    assert {"skill_list", "skill_install_url", "skill_read_resource"} <= names


def test_online_skill_rejects_local_addresses():
    for url in ("http://127.0.0.1/skill.zip", "http://192.168.1.2/SKILL.md", "http://demo.local/a.zip"):
        try:
            skills.install_url(url)
        except ValueError as exc:
            assert "本机" in str(exc) or "内网" in str(exc)
        else:
            raise AssertionError(url)
