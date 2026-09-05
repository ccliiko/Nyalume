"""人设注册表行为（不写运行时 persona_config.json）。"""

from mini_agent.core import personas


def test_personas_list_has_cliko_and_assistant():
    ids = [p["id"] for p in personas.list_personas()]
    assert "cliko" in ids
    assert "assistant" in ids
    assert "phoebe" not in ids


def test_unknown_id_falls_back_to_default():
    meta = personas.get_persona("no_such_persona")
    assert meta["id"] == personas.DEFAULT_PERSONA


def test_persona_base_prompt_injects_internal_state():
    text = personas.persona_base_prompt("cliko", 66)
    assert "内部状态" in text
    assert "好感度 66" in text


def test_assistant_persona_has_no_affection():
    assert personas.get_persona("assistant")["use_affection"] is False
