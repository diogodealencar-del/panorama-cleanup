"""Summarize observed PAN-OS Traffic CSV rows for a pair of rule inventories."""
from __future__ import annotations

import codecs
from collections import Counter, defaultdict
import csv
from datetime import datetime
from pathlib import Path
import re


def header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def rule_uuid(value: str) -> str:
    value = value.strip().lower().strip("{}")
    return "" if not value.replace("0", "").replace("-", "") or value in {"n/a", "none", "unknown", "-"} else value


ALIASES = {
    "rule": ("rule", "rulename", "securityrule"),
    "uuid": ("uuidforrule", "ruleuuid", "securityruleuuid"),
    "scope": ("devicegroup", "devicegroupname", "dgname"),
    "serial": ("serial", "serialnumber", "deviceserial"),
    "vsys": ("virtualsystem", "vsys", "virtualsystemname"),
    "session": ("sessionid",), "start": ("starttime", "sessionstarttime"),
    "time": ("generatetime", "generatedtime", "receivetime", "timelogged"),
    "type": ("type", "logtype"), "action": ("action",),
    "source_zone": ("sourcezone", "fromzone"), "source": ("sourceaddress", "sourceip", "source"),
    "destination_zone": ("destinationzone", "tozone"), "destination": ("destinationaddress", "destinationip", "destination"),
    "application": ("application", "app"), "protocol": ("ipprotocol", "protocol"),
    "destination_port": ("destinationport", "dport"),
}
FLOW_FIELDS = ("source_zone", "source", "destination_zone", "destination", "application", "protocol", "destination_port")


def csv_encoding(path: Path) -> str:
    with path.open("rb") as source:
        sample = source.read(65536)
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        codecs.getincrementaldecoder("utf-8-sig")().decode(sample, final=False)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"


def csv_columns(line: str):
    for delimiter in (",", ";", "\t"):
        names = list(map(header, next(csv.reader([line], delimiter=delimiter), [])))
        columns = {key: [names.index(alias) for alias in aliases if alias in names]
                   for key, aliases in ALIASES.items()}
        if len(names) > 1 and (columns["rule"] or columns["uuid"]):
            return delimiter, columns, len(names)
    raise ValueError("CSV sem cabeçalho de tráfego reconhecido. Exporte com a coluna Rule ou UUID for rule.")


def timestamp(text: str) -> str:
    if not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}", text):
        return ""
    try:
        return datetime.fromisoformat(text.replace("/", "-", 2)).isoformat(sep=" ")
    except ValueError:
        return ""


def traffic(path: Path, pairs):
    by_uuid = defaultdict(set)
    by_name = defaultdict(set)
    by_scope_name = defaultdict(set)
    for number, (before, after, _) in enumerate(pairs, 1):
        for rule in (before, after):
            if rule is None or rule.base.split("/")[-1] != "security":
                continue
            uid = rule_uuid(rule.element.get("uuid", ""))
            if uid:
                by_uuid[uid].add(number)
            by_name[rule.name].add(number)
            by_scope_name[rule.scope, rule.name].add(number)

    counts = Counter()
    rule_rows = defaultdict(Counter)
    flow_rows = Counter()
    unlinked = Counter()
    session_keys = defaultdict(set)
    intervals = {}
    coverage = ["", ""]

    def update_interval(interval, value):
        if value:
            interval[0] = min(interval[0], value) if interval[0] else value
            interval[1] = max(interval[1], value) if interval[1] else value

    with path.open(encoding=csv_encoding(path), newline="") as source:
        first = source.readline()
        while first and not first.strip():
            first = source.readline()
        delimiter, columns, expected_width = csv_columns(first)
        for row in csv.reader(source, delimiter=delimiter):
            if not any(row):
                continue
            counts["Linhas lidas"] += 1
            if len(row) != expected_width:
                counts["Linhas com colunas inválidas"] += 1
                continue

            def value(key):
                return next((row[i].strip() for i in columns[key] if row[i].strip()), "")

            log_type = value("type").casefold()
            if log_type and log_type not in {"traffic", "tráfego", "trafego"}:
                counts["Linhas de outro tipo"] += 1
                continue
            counts["Linhas de tráfego"] += 1
            time = timestamp(value("time"))
            update_interval(coverage, time)
            name, uid, scope = value("rule"), rule_uuid(value("uuid")), value("scope")
            if scope.casefold() == "shared":
                scope = "shared"
            if uid:
                matches, method = by_uuid.get(uid, ()), "UUID"
            elif scope:
                matches, method = by_scope_name.get((scope, name), ()), "DG + nome"
            else:
                matches, method = by_name.get(name, ()), "Nome único"
            if len(matches) != 1:
                reason = "Ambígua" if matches else ("UUID ausente nos XMLs" if uid else "Regra ausente ou fora do escopo")
                unlinked[reason, name, uid, scope] += 1
                counts["Linhas sem vínculo"] += 1
                continue

            rule = next(iter(matches))
            action = value("action").casefold()
            stat = rule_rows[rule]
            counts["Linhas vinculadas"] += 1
            stat["Linhas"] += 1
            stat[method] += 1
            category = {"allow": "Allow", "deny": "Deny", "drop": "Drop"}.get(action, "Outras")
            stat[category] += 1
            update_interval(intervals.setdefault(rule, ["", ""]), time)
            serial, vsys, sid, start = (value(k) for k in ("serial", "vsys", "session", "start"))
            start = timestamp(start)
            if serial and vsys and sid.isdigit() and int(sid) > 0 and start:
                session_keys[rule].add((serial, vsys, sid, start))
            else:
                stat["Linhas sem chave de sessão"] += 1
            flow_rows[(rule, action, *(value(k) for k in FLOW_FIELDS))] += 1

    for rule, stat in rule_rows.items():
        stat["Sessões identificáveis"] = len(session_keys.get(rule, ()))
    return counts, rule_rows, flow_rows, unlinked, intervals, coverage
