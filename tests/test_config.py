import json

import pytest

from panorama_cleanup.config import DEFAULT_SETTINGS_FILE, PROJECT_ROOT, load_settings
from panorama_cleanup.errors import ConfigError
from tests.conftest import SETTINGS


def write(tmp_path, data):
    (tmp_path / "config").mkdir(exist_ok=True)
    path = tmp_path / "config" / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_default_settings_load():
    s = load_settings(DEFAULT_SETTINGS_FILE)
    assert s.root == PROJECT_ROOT
    assert s.input_dir == PROJECT_ROOT / "input"
    assert s.input_dir not in s.writable_dirs


def test_missing_key_is_error(tmp_path):
    data = json.loads(json.dumps(SETTINGS))
    del data["paths"]["logs"]
    with pytest.raises(ConfigError, match="ausentes"):
        load_settings(write(tmp_path, data))


def test_unknown_key_is_error(tmp_path):
    data = json.loads(json.dumps(SETTINGS))
    data["paths"]["extra"] = "x"
    with pytest.raises(ConfigError, match="desconhecidas"):
        load_settings(write(tmp_path, data))


def test_path_outside_project_is_error(tmp_path):
    data = json.loads(json.dumps(SETTINGS))
    data["paths"]["reports"] = "../fora"
    with pytest.raises(ConfigError, match="fora do projeto"):
        load_settings(write(tmp_path, data))


def test_output_inside_input_is_error(tmp_path):
    data = json.loads(json.dumps(SETTINGS))
    data["paths"]["reports"] = "input/reports"
    with pytest.raises(ConfigError, match="sobrepor"):
        load_settings(write(tmp_path, data))


def test_invalid_log_level_is_error(tmp_path):
    data = json.loads(json.dumps(SETTINGS))
    data["logging"]["level"] = "VERBOSE"
    with pytest.raises(ConfigError):
        load_settings(write(tmp_path, data))


def test_missing_file_is_error(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "nao-existe.json")
