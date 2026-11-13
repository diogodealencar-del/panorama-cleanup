import csv
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from panorama_cleanup.comparison import compare_report


RULE_UUID = "c0ff0011-1234-4321-8765-001234567abc"


def config(name, address):
    return f'''<config version="11.1.0">
    <shared><address><entry name="HOST"><ip-netmask>{address}</ip-netmask></entry></address></shared>
    <devices><entry name="localhost.localdomain"><device-group>
      <entry name="A"><post-rulebase><security><rules>
        <entry name="{name}" uuid="{RULE_UUID}"><source><member>HOST</member></source><action>allow</action></entry>
        <entry name="DUP"><action>allow</action></entry>
      </rules></security></post-rulebase></entry>
      <entry name="B"><post-rulebase><security><rules>
        <entry name="DUP"><action>allow</action></entry>
      </rules></security></post-rulebase></entry>
    </device-group></entry></devices></config>'''


class ComparisonTest(unittest.TestCase):
    def test_rule_rename_and_traffic_linkage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            initial, final, logs, output = (root / name for name in ("original.xml", "final.xml", "traffic.csv", "result.xlsx"))
            initial.write_text(config("OLD", "192.0.2.1"), encoding="utf-8")
            final.write_text(config("NEW", "192.0.2.2"), encoding="utf-8")
            start = "2026/11/10 10:00:00"
            with logs.open("w", newline="", encoding="utf-8-sig") as out:
                rows = csv.writer(out, delimiter=";")
                rows.writerow(("Rule", "UUID for rule", "Device Group", "Serial #", "Virtual System", "Session ID", "Start Time", "Generate Time", "Type", "Action", "Application"))
                for row in (
                    ("OLD", RULE_UUID, "A", "123", "vsys1", "25", start, start, "TRAFFIC", "allow", "=1+1"),
                    ("OLD", RULE_UUID, "A", "123", "vsys1", "25", start, "2026/11/10 10:01:00", "TRAFFIC", "allow", "=1+1"),
                    ("OLD", "unknown-id", "A", "123", "vsys1", "26", start, start, "TRAFFIC", "deny", "ssl"),
                    ("DUP", "", "", "123", "vsys1", "27", start, start, "TRAFFIC", "deny", "ssl"),
                    ("DUP", "", "B", "123", "vsys1", "28", start, start, "TRAFFIC", "deny", "ssl"),
                    ("OLD", "", "", "123", "vsys1", "25", "2026/11/10 10:10:00", "2026/11/10 10:11:00", "TRAFFIC", "allow", "ssl"),
                    ("OLD", RULE_UUID, "A", "123", "vsys1", "29", start, start, "THREAT", "alert", "ssl"),
                    ("linha incompleta",),
                ):
                    rows.writerow(row)
            result = compare_report(initial, final, output, logs)
            self.assertEqual(result["rules_changed"], 1)
            self.assertEqual(result["changed_rules_with_traffic"], 1)
            with closing(load_workbook(output, read_only=True)) as wb:
                summary = dict(wb["Resumo"].values)
                self.assertEqual(summary["Objetos alterados"], 1)
                self.assertEqual(summary["Linhas vinculadas"], 4)
                self.assertEqual(summary["Linhas sem vínculo"], 2)
                self.assertEqual(summary["Linhas com colunas inválidas"], 1)
                headers, *records = wb["Regras"].values
                row = next(dict(zip(headers, r)) for r in records if r[4] == "NEW")
                self.assertEqual(row["Regra inicial"], "OLD")
                self.assertEqual(row["Linhas"], 3)
                self.assertEqual(row["Sessões identificáveis"], 2)
                self.assertEqual(row["UUID"], 2)
                self.assertEqual(row["Nome único"], 1)
                cells = [c for r in wb["Fluxos observados"].iter_rows() for c in r if c.value == "=1+1"]
                self.assertEqual([c.data_type for c in cells], ["s"])

    def test_report_destination_and_invalid_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            initial, output, logs = (root / name for name in ("original.xml", "result.xlsx", "bad.csv"))
            initial.write_text(config("OLD", "192.0.2.1"), encoding="utf-8")
            logs.write_text("a,b\n1,2\n", encoding="utf-8")
            output.write_bytes(b"existing report")
            with self.assertRaisesRegex(ValueError, "cabeçalho"):
                compare_report(initial, initial, output, logs)
            self.assertEqual(output.read_bytes(), b"existing report")
            with self.assertRaisesRegex(ValueError, "destino"):
                compare_report(initial, initial, initial)
