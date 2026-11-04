from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.policy_tests import _ops_pairs, build_tests, check_ops_lines
from panorama_cleanup.report_generator import plan_object_rows
from panorama_cleanup.planner import build_plan
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES

PATTERNS = {
    "test security-policy-match": [_ops_pairs(
        " from <value> to <value>|<multicast> source <ip/netmask> source-port <1-65535> "
        "destination <ip/netmask> destination-port <1-65535> source-user <value> protocol <1-255> "
        "show-all <yes|no> application <value>")],
    "test nat-policy-match": [_ops_pairs(
        " from <value>|<any> to <value>|<any> source <ip/netmask> destination <ip/netmask> "
        "source-port <1-65535> destination-port <1-65535> protocol <1-255>")],
}


def test_ops_check_accepts_documented_subsets():
    lines = ["test security-policy-match destination 10.0.0.1",
             "test security-policy-match from trust to untrust source 1.1.1.1 destination 2.2.2.2 "
             "destination-port 443 protocol 6 application ssl",
             "test nat-policy-match from any to any destination 2.2.2.2"]
    assert all(ok for _, ok in check_ops_lines(lines, PATTERNS))


def test_ops_check_rejects_unknown_or_out_of_order():
    lines = ["test security-policy-match destination 1.1.1.1 from trust",
             "test security-policy-match dst 1.1.1.1",
             "test pbf-policy-match destination 1.1.1.1"]
    assert not any(ok for _, ok in check_ops_lines(lines, PATTERNS))


def test_tests_generated_for_renamed_rule():
    an = run_analysis(parse_backup(FIXTURES / "panorama_deps.xml"))
    plan = build_plan(an)
    decisions = {d["xpath"]: d for d in plan_object_rows(an, plan)}
    renames = {(u["source_xpath"], u["field"], u["old"]): u["new"] for u in plan.rule_updates}
    tests = build_tests(an, decisions, renames)
    renamed = [t for t in tests if t.reason == "referência renomeada"]
    assert [(t.device_group, t.rule) for t in renamed] == [("DG-RJ", "RJ-R1")]
    assert renamed[0].command.startswith("test security-policy-match from trust to untrust")
    assert "destination 10.0.0.99" in renamed[0].command
    assert all(ok for _, ok in check_ops_lines([t.command for t in tests], PATTERNS))
