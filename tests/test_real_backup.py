"""Conferência sobre os backups reais em input/ (ignorado se não houver nenhum).

As contagens esperadas são obtidas por XPath direto, independente do inventário.
"""

import pytest

from panorama_cleanup.inventory import build_inventory
from panorama_cleanup.run_context import sha256_file
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import PROJECT_ROOT

BACKUPS = sorted((PROJECT_ROOT / "input").glob("*.xml"))


@pytest.mark.skipif(not BACKUPS, reason="nenhum backup em input/")
@pytest.mark.parametrize("backup", BACKUPS, ids=lambda p: p.name)
def test_counts_match_direct_xpath(backup):
    digest = sha256_file(backup)
    tree = parse_backup(backup)
    inv = build_inventory(tree)
    assert sha256_file(backup) == digest

    root = tree.getroot()
    dg_base = "devices/entry/device-group/entry"
    assert len(inv.device_groups) == len(root.findall(dg_base))

    for scope, rel, _, count in inv.reconciliation:
        base = "shared" if scope == "shared" else f"{dg_base}[@name='{scope}']"
        assert count == len(root.findall(f"{base}/{rel}/entry")), (scope, rel)

    for kind in ("address", "address-group", "service", "service-group", "tag"):
        direct = len(root.findall(f"shared/{kind}/entry")) + len(root.findall(f"{dg_base}/{kind}/entry"))
        assert sum(1 for r in inv.records if r.object_type == kind) == direct, kind
