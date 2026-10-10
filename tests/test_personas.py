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


def test_only_nyalume_prompt_includes_the_restored_worldview():
    expected = personas.NYALUME_SYSTEM_PROMPT + personas.WORLD_VIEW_PROMPT
    assert personas.persona_base_prompt('nyalume') == expected
    assert personas.persona_base_prompt('assistant') == personas.ASSISTANT_SYSTEM_PROMPT
    assert personas.persona_base_prompt('unknown') == expected


def test_invalid_config_and_environment_fall_back_to_a_valid_persona(tmp_path, monkeypatch):
    config = tmp_path / 'persona.json'
    monkeypatch.setattr(personas, 'CONFIG_PATH', str(config))
    monkeypatch.setenv('PERSONA', 'unknown')
    for contents in ['[]', 'null', '42', '{invalid', '{"persona":"unknown"}']:
        config.write_text(contents, encoding='utf-8')
        assert personas.resolve_persona_id() == personas.DEFAULT_PERSONA
    monkeypatch.setenv('PERSONA', 'assistant')
    assert personas.resolve_persona_id() == 'assistant'
    assert personas.set_persona('nyalume')
    assert personas.resolve_persona_id() == 'nyalume'
