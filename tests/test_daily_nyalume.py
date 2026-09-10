"""今日 Nyalume：分档、每日唯一、记录和风格注入。"""

from pathlib import Path

from nyalume.core import daily_nyalume, memory
from nyalume.frontends.pet import interactions
from nyalume.frontends.pet import pets_registry
from nyalume.frontends.pet.pet import _pet_scale
from nyalume.frontends.web import server


def test_all_scores_map_to_eleven_profiles():
    assert len(daily_nyalume.PROFILES) == 11
    assert len({daily_nyalume.profile_for_score(i)["key"] for i in range(101)}) == 11
    assert daily_nyalume.profile_for_score(88)["key"] == "reliable"
    assert daily_nyalume.profile_for_score(89)["key"] == "lucky"
    assert daily_nyalume.profile_for_score(99)["key"] == "lucky"
    assert daily_nyalume.profile_for_score(100)["key"] == "perfect"


def test_pet_touch_still_has_local_feedback():
    for region in ("head", "body", "legs", "miss"):
        assert interactions.pick_line(region)


def test_placeholder_pet_is_not_selectable(monkeypatch, tmp_path):
    monkeypatch.setattr(pets_registry, "USER_PETS_DIR", str(tmp_path))
    assert pets_registry.list_pets() == []
    assert pets_registry.get_pet("neko-placeholder")["id"] == "nyalume"


def test_pet_manifest_keeps_face_anchor_and_scale_is_downsample_only():
    pet = pets_registry.get_pet("nyalume")
    assert pet["face"] == {"cx": 0.38, "cy": 0.28, "fr": 0.05}
    assert _pet_scale(None) == 0.75
    assert _pet_scale(0.62) == 0.6
    assert _pet_scale(2) == 1.0


def test_all_profiles_use_collectible_card_art_and_non_gold_finishes():
    finishes = []
    for profile in daily_nyalume.PROFILES:
        response = server.daily_nyalume_portrait(profile["key"])
        assert Path(response.path).name == f"{profile['key']}.png"
        assert Path(response.path).is_file()
        finishes.append(profile["finish"])

    assert len(set(finishes)) == len(daily_nyalume.PROFILES)
    assert not {"gold", "golden", "metallic-gold"}.intersection(finishes)


def test_draw_once_records_and_injects_style(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setattr(daily_nyalume, "RECORD_PATH", str(tmp_path / "nyalume_blessings.md"))
    monkeypatch.setattr(daily_nyalume, "_today", lambda: "2026-09-08")
    monkeypatch.setattr(daily_nyalume.secrets, "randbelow", lambda _n: 100)
    memory.init_db()

    first = daily_nyalume.draw_today()
    second = daily_nyalume.draw_today()
    assert first["new"] is True and second["new"] is False
    assert first["score"] == 100 and first["name"] == "满分 Nyalume"
    assert first["finish"] == "crystal"
    assert first["blessing"] == second["blessing"]
    assert "满分 Nyalume" in daily_nyalume.style_prompt()
    record = (tmp_path / "nyalume_blessings.md").read_text(encoding="utf-8")
    assert record.count("| 2026-09-08 |") == 1


def test_blessing_language_is_mostly_chinese(monkeypatch):
    monkeypatch.setattr(memory, "list_daily_nyalume", lambda: [])
    monkeypatch.setattr(daily_nyalume.secrets, "choice", lambda rows: rows[0])

    monkeypatch.setattr(daily_nyalume.secrets, "randbelow", lambda _n: 5)
    assert daily_nyalume._pick_blessing().get("language", "zh") == "zh"

    monkeypatch.setattr(daily_nyalume.secrets, "randbelow", lambda _n: 4)
    assert daily_nyalume._pick_blessing()["language"] != "zh"
