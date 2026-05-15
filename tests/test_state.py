import pytest

from coda2linear.state import load_state


def test_load_state_returns_empty_structure_when_missing(tmp_path):
    assert load_state(str(tmp_path / "state.json")) == {"pages": {}, "uploaded_assets": {}}


def test_load_state_explains_invalid_json_recovery(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="state.json is not valid JSON"):
        load_state(str(state_path))
