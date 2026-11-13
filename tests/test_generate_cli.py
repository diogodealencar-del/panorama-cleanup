import subprocess

import pytest
from lxml import etree

from panorama_cleanup.generate_cli import (
    ApiOp, CommandError, build_api_ops, cli_commands, same_config, simulate_xmlapi, xpath_tokens,
)
from tests.test_cleanup import converted
from tests.conftest import FIXTURES

DG = "/config/devices/entry[@name='localhost.localdomain']/device-group/entry[@name='DG-RJ']"


def op(action, xpath, element=""):
    return ApiOp(1, 1, "CREATE_SHARED", action, xpath, element, "", "")


def test_xpath_tokens():
    assert xpath_tokens("/config/shared/address/entry[@name='A B']") == ["shared", "address", "A B"]
    assert xpath_tokens(f"{DG}/post-rulebase/security/rules/entry[@name='R 1']/destination") == [
        "device-group", "DG-RJ", "post-rulebase", "security", "rules", "R 1", "destination"]
    with pytest.raises(CommandError):
        xpath_tokens("/config/devices/entry[@name='localhost.localdomain']/template/entry[@name='T']")


def test_create_address_cli():
    el = "<entry name=\"WEB 01\"><ip-netmask>10.0.0.1</ip-netmask><description>srv web</description>" \
         "<tag><member>PROD</member></tag></entry>"
    assert cli_commands(op("set", "/config/shared/address", el)) == [
        'set shared address "WEB 01" ip-netmask 10.0.0.1',
        'set shared address "WEB 01" description "srv web"',
        'set shared address "WEB 01" tag [ PROD ]',
    ]


def test_create_service_and_group_cli():
    svc = "<entry name=\"TCP-443\"><protocol><tcp><port>443</port><override><no/></override></tcp></protocol></entry>"
    assert cli_commands(op("set", "/config/shared/service", svc)) == [
        "set shared service TCP-443 protocol tcp port 443",
        "set shared service TCP-443 protocol tcp override no",
    ]
    grp = "<entry name=\"G\"><static><member>A</member><member>B C</member></static></entry>"
    assert cli_commands(op("set", "/config/shared/address-group", grp)) == [
        'set shared address-group G static [ A "B C" ]']


def test_rule_field_edit_cli_preserves_order():
    xpath = f"{DG}/post-rulebase/security/rules/entry[@name='R1']/destination"
    el = "<destination><member>Z</member><member>A_DG-RJ</member></destination>"
    assert cli_commands(ApiOp(1, 2, "UPDATE_RULE_REFERENCE", "edit", xpath, el, "", "")) == [
        'delete device-group DG-RJ post-rulebase security rules R1 destination',
        'set device-group DG-RJ post-rulebase security rules R1 destination [ Z A_DG-RJ ]',
    ]


def test_leaf_field_edit_cli():
    xpath = f"{DG}/post-rulebase/nat/rules/entry[@name='N1']/service"
    assert cli_commands(ApiOp(1, 2, "UPDATE_RULE_REFERENCE", "edit", xpath,
                              "<service>S_DG-RJ</service>", "", ""))[-1] == (
        "set device-group DG-RJ post-rulebase nat rules N1 service S_DG-RJ")


def test_delete_cli():
    assert cli_commands(ApiOp(1, 3, "DELETE_LOCAL_COPY", "delete",
                              f"{DG}/address/entry[@name='X']", "", "", "")) == [
        "delete device-group DG-RJ address X"]


@pytest.mark.parametrize("text", ["linha1\nlinha2", 'com "aspas"', "com [colchete]", "Máquina",
                                  "barra \\ invertida"])
def test_unsafe_values_have_no_cli(text):
    el = etree.Element("entry", name="X")
    etree.SubElement(el, "description").text = text
    with pytest.raises(CommandError):
        cli_commands(op("set", "/config/shared/address", etree.tostring(el, encoding="unicode")))


def test_api_ops_reproduce_converted_and_final():
    from panorama_cleanup.analysis import run_analysis
    from panorama_cleanup.cleanup import deletion_operations, review_candidates
    from panorama_cleanup.shared_converter import apply_plan

    original, ops, res, values = converted(FIXTURES / "panorama_deps.xml")
    cands = review_candidates(res.tree, run_analysis(res.tree), ops, values)
    confirmed = deletion_operations(cands)
    final = apply_plan(res.tree, confirmed, [], phases=(4,)).tree
    api = build_api_ops(ops, confirmed, res.tree, final, original)
    wrapped = [o for o in api if o.action == "set" and o.xpath == "/config/shared"]
    assert {etree.fromstring(o.element).tag for o in wrapped} == {"application-group", "application-filter"}
    assert sorted(line for o in wrapped for line in cli_commands(o)) == [
        "set shared application-filter FILTER-1 risk [ 5 ]",
        "set shared application-group APPS-WEB members [ web-browsing ]",
    ]
    assert same_config(simulate_xmlapi(original, [o for o in api if o.phase < 4]), res.tree)
    assert same_config(simulate_xmlapi(original, api), final)
    assert not same_config(simulate_xmlapi(original, api[:-1]), final)


def test_runner_dry_run_sends_nothing(tmp_path):
    from panorama_cleanup.generate_cli import RUNNER

    runner = tmp_path / "run-xmlapi.sh"
    runner.write_text(RUNNER)
    tsv = tmp_path / "ops.tsv"
    tsv.write_text("seq\tphase\taction\txpath\telement\n1\t1\tdelete\t/config/shared/address/entry[@name='X']\t\n")
    result = subprocess.run(["bash", str(runner), str(tsv)], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0
    assert "nada será enviado" in result.stdout
    assert subprocess.run(["bash", "-n", str(runner)]).returncode == 0


class _Meta(dict):
    """meta mínimo para documents(): qualquer chave ausente vira um texto fixo."""

    def __missing__(self, key):
        return "X"


@pytest.mark.parametrize("counts, expected", [
    ({0: 94, 1: 10, 2: 5, 3: 12, 4: 7, 5: 3},
     ["05-fase0-copias-locais", "10-fase1-criar-shared", "20-fase2-atualizar-referencias",
      "30-fase3-excluir-copias-locais", "40-fase4-excluir-nao-utilizados",
      "41-fase4-excluir-tags-nao-utilizadas"]),
    ({0: 0, 1: 10, 2: 5, 3: 12, 4: 7, 5: 0},
     ["10-fase1-criar-shared", "20-fase2-atualizar-referencias",
      "30-fase3-excluir-copias-locais", "40-fase4-excluir-nao-utilizados"]),
])
def test_readme_order_lists_every_phase_file(counts, expected):
    """Regressão: a fase 0 era gravada mas não constava da ordem de execução do LEIA-ME."""
    import re
    from panorama_cleanup.generate_cli import documents

    readme = documents(_Meta(device_groups=["DG-RJ"], containers=[]), counts)["00-LEIA-ME.md"]
    order = readme.split("## Ordem de execução", 1)[1].split("\n\n", 2)[1]
    rows = [line for line in order.splitlines() if line.startswith("| ") and "Passo" not in line
            and not line.startswith("| — |")]
    stems = [m.group(1) for line in rows for m in [re.search(r"`(\d\d-fase[^`.]+)\.tsv`", line)] if m]
    assert stems == expected
    assert [line.split("|")[1].strip() for line in rows] == [str(i) for i in range(len(rows))]
    assert rows[0].startswith("| 0 | `01-precheck.md`") and "02-backup.cli" in rows[1]
    assert "60-commit-e-push.md" in rows[-1]
    if counts[0]:
        assert f"| `05-fase0-copias-locais.tsv` / `.cli` | {counts[0]} |" in readme
