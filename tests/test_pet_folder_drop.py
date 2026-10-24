from unittest.mock import Mock

import pytest

from nyalume.frontends.pet import pet


@pytest.mark.parametrize("opened", [True, False])
def test_folder_drop_returns_to_idle_only_after_session_opens(monkeypatch, tmp_path, opened):
    app = pet.PetApp.__new__(pet.PetApp)
    app.window = Mock()
    app.chat = Mock()
    monkeypatch.setattr(pet.memory, "add_project", Mock(return_value={"id": "project"}))
    monkeypatch.setattr(pet.memory, "create_session", Mock(return_value="session"))

    def open_session(session_id):
        assert session_id == "session"
        app.window.eat.assert_called_once_with(3400)
        app.window.return_to_idle.assert_not_called()
        return opened

    app.chat.open_session.side_effect = open_session
    app._on_folder_drop([str(tmp_path)])

    pet.memory.add_project.assert_called_once_with(str(tmp_path))
    pet.memory.create_session.assert_called_once_with(project="project")
    app.chat.open_session.assert_called_once_with("session")
    assert app.window.return_to_idle.call_count == int(opened)
