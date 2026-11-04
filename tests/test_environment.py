import pytest

from panorama_cleanup.environment import (
    check_environment, check_python, parse_requirements, version_tuple,
)
from tests.conftest import PROJECT_ROOT


def test_real_environment_satisfies_requirements():
    failed = [c for c in check_environment(PROJECT_ROOT / "requirements.txt") if not c.ok]
    assert failed == []


def test_parse_requirements(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("# comentário\nlxml>=5.0\n\npytest>=8.0  # testes\n", encoding="utf-8")
    assert parse_requirements(req) == [("lxml", "5.0"), ("pytest", "8.0")]


@pytest.mark.parametrize("line", ["lxml", "lxml==5.0", "lxml>5", "-e ."])
def test_parse_requirements_rejects_unsupported_forms(tmp_path, line):
    req = tmp_path / "r.txt"
    req.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_requirements(req)


def test_version_comparison_is_numeric():
    assert version_tuple("10.0") > version_tuple("9.9")
    assert version_tuple("6.1.3") >= version_tuple("5.0")
    assert version_tuple("2.0rc1") == (2, 0)


def test_missing_package_fails(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("pacote-que-nao-existe-xyz>=1.0\n", encoding="utf-8")
    check = check_environment(req)[1]
    assert not check.ok and check.found == "ausente"


def test_python_version_rules():
    assert not check_python((3, 8, 18)).ok
    old = check_python((3, 9, 6))
    assert old.ok and old.warning
    assert check_python((3, 12, 0)).warning == ""
