import pytest

from panorama_cleanup.cli_reference import check_lines, command_lines, load_patterns
from tests.conftest import PROJECT_ROOT

GUIDE = PROJECT_ROOT / "docs" / "referencias" / "pan-os-11.1" / "ngfw-cli.md"

SAMPLE = """Texto introdutório.
set shared address <name> ip-netmask <ip/netmask>
set shared address <name> description <value>
set shared address-group <name> static [ <static1>
<static2>... ]
set shared tag <name> color <color1|color2|color3>
set device-group <name> post-rulebase security rules <name>
destination [ <destination1> <destination2>... ]
PAN-OS CLI Quick Start Version 11.1 & later 192 ©2026 Palo Alto Networks, Inc.
set shared service <name> protocol tcp override no
When pasting commands, check the order.
"""


@pytest.fixture
def patterns(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text(SAMPLE, encoding="utf-8")
    return load_patterns(path)


@pytest.mark.parametrize("line", [
    'set shared address "WEB 01" ip-netmask 10.0.0.1',
    'set shared address-group G static [ A "B C" ]',
    "set shared tag T color color2",
    'set device-group DG post-rulebase security rules "R 1" destination [ X Y ]',
    "set shared service S protocol tcp override no",
    "delete shared address X",
    'delete device-group DG post-rulebase security rules "R 1" destination',
])
def test_documented_forms_accepted(patterns, line):
    (result,) = check_lines([line], patterns)
    assert result.ok, result


@pytest.mark.parametrize("line", [
    "set shared address X ip-mask 10.0.0.1",          # palavra-chave inexistente
    "set shared address-group G static A",            # lista exigida
    "set shared address X ip-netmask [ 1.1.1.1 ]",    # valor simples exigido
    "set shared tag T color color9",                  # fora das opções
    "set shared address X",                           # caminho incompleto como set
    "set shared address X ip-netmask 1.1.1.1 extra",  # token a mais
    "delete shared adress X",                         # erro de digitação
    "show shared address X",                          # não é set/delete
])
def test_undocumented_forms_rejected(patterns, line):
    (result,) = check_lines([line], patterns)
    assert not result.ok


def test_command_lines_ignores_comments():
    text = "# comentário\nset shared tag T\n\ndelete shared tag T\nconfigure\n"
    assert command_lines(text) == ["set shared tag T", "delete shared tag T"]


@pytest.mark.skipif(not GUIDE.is_file(), reason="guia oficial ausente")
def test_real_guide_contains_forms_used_by_generator():
    patterns = load_patterns(GUIDE)
    lines = [
        "set shared address X ip-netmask 10.0.0.1",
        "set shared address X description d",
        "set shared address X tag [ A ]",
        "set shared address-group G static [ A B ]",
        "set shared service S protocol tcp port 443",
        "set shared service S protocol udp port 53",
        "set shared service S protocol tcp override no",
        "set shared service-group SG members [ A ]",
        "set device-group D post-rulebase security rules R destination [ A ]",
        "set device-group D post-rulebase nat rules R service S",
        "delete device-group D address X",
        "delete shared tag T",
    ]
    assert [r.line for r in check_lines(lines, patterns) if not r.ok] == []
    assert not check_lines(["set shared address X ip-mask 1.1.1.1"], patterns)[0].ok


# Quebras reais do PDF do PAN-OS CLI Quick Start (hierarquias 12.1/12.2): listas de
# opções partidas no meio e alternativas "<value>|<any>". Antes da correção, a lista
# virava palavras-chave extras e até "color7" era reprovado.
WRAPPED = """set shared tag <name> color <color1|color2|color3|color4|color5|
color6|color7|color8|color36|
color37|color38>
set shared profiles virus <name> decoder <name> action <default|
allow|alert|drop|reset-client|reset-server|reset-both>
set shared profiles virus <name> decoder <name> mlav-
action <default|allow>
set shared profiles vulnerability <name> rules <name> threat-name
<value>|<any>
set shared profiles vulnerability <name> rules <name> host <any|
client|server>
"""


@pytest.fixture
def wrapped(tmp_path):
    path = tmp_path / "wrapped.md"
    path.write_text(WRAPPED, encoding="utf-8")
    return load_patterns(path)


@pytest.mark.parametrize("line", [
    "set shared tag AMCOM color color36",
    "set shared tag T1 color color7",
    "set shared tag T2 color color38",
    "set shared profiles virus av decoder http2 action allow",
    "set shared profiles virus av decoder imap mlav-action default",
    "set shared profiles vulnerability ips rules r1 threat-name any",
    "set shared profiles vulnerability ips rules r1 threat-name 30001",
    "set shared profiles vulnerability ips rules r1 host client",
])
def test_wrapped_choice_lists_are_joined(wrapped, line):
    assert check_lines([line], wrapped)[0].ok, line


@pytest.mark.parametrize("line", [
    "set shared tag BAD color color99",
    "set shared tag BAD color color6|color7",
    "set shared profiles virus av decoder http2 action block",
    "set shared profiles vulnerability ips rules r1 host banana",
    "set shared profiles vulnerability ips rules r1 threat-name any extra",
])
def test_wrapped_lists_still_reject_invalid_values(wrapped, line):
    assert not check_lines([line], wrapped)[0].ok, line
