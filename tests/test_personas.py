"""人设注册表行为（不写运行时 persona_config.json）。"""

from nyalume.core import personas


def test_personas_list_has_nyalume_and_assistant():
    ids = [p["id"] for p in personas.list_personas()]
    assert "nyalume" in ids
    assert "assistant" in ids
    assert "phoebe" not in ids


def test_unknown_id_falls_back_to_default():
    meta = personas.get_persona("no_such_persona")
    assert meta["id"] == personas.DEFAULT_PERSONA


def test_persona_base_prompt_has_no_relationship_score():
    text = personas.persona_base_prompt("nyalume")
    assert "内部状态" not in text
    assert "好感度" not in text


def test_personas_have_no_relationship_state():
    expected = {"id", "name", "prompt"}
    assert set(personas.get_persona("nyalume")) == expected
    assert set(personas.get_persona("assistant")) == expected
