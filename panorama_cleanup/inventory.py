"""Inventário de objetos por escopo: Shared e cada Device Group (CLAUDE.md §4).

Somente leitura. Cada objeto é registrado com o valor normalizado, o XPath
absoluto e a linha de origem no backup, para rastreabilidade.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from panorama_cleanup.xml_reader import (
    SHARED, DeviceGroup, PanoramaMetadata, panorama_entry, read_device_groups, read_metadata,
)

# Contêineres de objetos reconhecidos, relativos ao escopo.
KNOWN_TYPES = (
    ("tag", "Tag"),
    ("address", "Address"),
    ("address-group", "Address Group"),
    ("service", "Service"),
    ("service-group", "Service Group"),
    ("application", "Application"),
    ("application-group", "Application Group"),
    ("application-filter", "Application Filter"),
    ("schedule", "Schedule"),
    ("external-list", "External Dynamic List"),
    ("region", "Region"),
    ("dynamic-user-group", "Dynamic User Group"),
    ("profile-group", "Security Profile Group"),
    ("log-settings/profiles", "Log Forwarding Profile"),
)
# Todo filho de <profiles> é um tipo de Security Profile (virus, spyware, ...).
PROFILES = "profiles"
# Seções do escopo que não são objetos; analisadas a partir da Etapa 3.
NON_OBJECT_SECTIONS = frozenset({
    "pre-rulebase", "post-rulebase", "devices", "reference-templates", "user-group-source",
})
# Elementos de primeiro nível da entry que são metadados, não valor.
METADATA_CHILDREN = ("description", "tag", "disable-override")
MEMBER_PATHS = ("static/member", "members/member")


@dataclass(frozen=True)
class ObjectRecord:
    scope: str
    scope_type: str
    object_type: str
    type_label: str
    recognized: bool
    name: str
    value: str
    value_sha256: str
    members: tuple
    description: str
    tags: tuple
    disable_override: str
    xpath: str
    source_line: int


@dataclass
class Inventory:
    metadata: PanoramaMetadata
    device_groups: list
    records: list
    warnings: list
    reconciliation: list = field(default_factory=list)
    # Elementos lxml paralelos a records (mesmo índice); não vão para relatórios.
    elements: list = field(default_factory=list, repr=False)

    def counts(self) -> Counter:
        return Counter((r.scope, r.object_type) for r in self.records)

    def scopes(self) -> list:
        return [SHARED] + [dg.name for dg in self.device_groups]


def build_inventory(tree: etree._ElementTree) -> Inventory:
    root = tree.getroot()
    metadata = read_metadata(tree)
    device_groups, warnings = read_device_groups(tree)
    device = panorama_entry(root)

    scopes = [(SHARED, "shared", root.find("shared"), scope_xpath(metadata, SHARED))]
    dg_elements = {e.get("name"): e for e in device.findall("device-group/entry")}
    for dg in device_groups:
        scopes.append(
            (dg.name, "device-group", dg_elements[dg.name], scope_xpath(metadata, dg.name))
        )

    records, elements, reconciliation = [], [], []
    for scope, scope_type, element, base in scopes:
        if element is None:
            warnings.append(f"Escopo {scope}: elemento ausente no XML")
            continue
        for rel, label, recognized in _containers(element, scope, warnings):
            entries = element.findall(f"{rel}/entry")
            scope_records = [
                _record(e, scope, scope_type, rel, label, recognized, base) for e in entries
            ]
            records.extend(scope_records)
            elements.extend(entries)
            # Contagem independente via XPath, comparada aos registros gerados.
            xml_count = int(element.xpath(f"count({rel}/entry)"))
            reconciliation.append((scope, rel, xml_count, len(scope_records)))
            _check_names(scope, rel, entries, warnings)

    return Inventory(metadata, device_groups, records, warnings, reconciliation, elements)


def scope_xpath(metadata: PanoramaMetadata, scope: str) -> str:
    """XPath absoluto (formato da XML API) de Shared ou de um Device Group."""
    if scope == SHARED:
        return "/config/shared"
    return (
        f"/config/devices/entry[@name={quote(metadata.panorama_entry)}]"
        f"/device-group/entry[@name={quote(scope)}]"
    )


def _containers(scope_el, scope: str, warnings: list) -> list:
    """Contêineres de objetos do escopo: conhecidos primeiro, depois não reconhecidos.

    Qualquer filho (ou neto) do escopo que contenha <entry> e não seja tipo
    conhecido nem seção de política é inventariado como não reconhecido.
    """
    found = []
    for rel, label in KNOWN_TYPES:
        if scope_el.find(rel) is not None:
            found.append((rel, label, True))
    profiles = scope_el.find(PROFILES)
    if profiles is not None:
        for child in profiles:
            if isinstance(child.tag, str):
                found.append((f"{PROFILES}/{child.tag}", f"Security Profile ({child.tag})", True))

    known = {rel for rel, _ in KNOWN_TYPES}
    for child in scope_el:
        if not isinstance(child.tag, str) or child.tag in NON_OBJECT_SECTIONS:
            continue
        if child.tag == PROFILES or child.tag in known:
            continue
        candidates = [(child.tag, child)]
        candidates += [(f"{child.tag}/{g.tag}", g) for g in child if isinstance(g.tag, str)]
        for rel, el in candidates:
            if rel in known or el.find("entry") is None:
                continue
            found.append((rel, f"Não reconhecido ({rel})", False))
            warnings.append(
                f"Escopo {scope}: contêiner não reconhecido {rel!r} com "
                f"{len(el.findall('entry'))} entradas (inventariado, sem classificação)"
            )
    return found


def _record(entry, scope, scope_type, rel, label, recognized, base) -> ObjectRecord:
    name = entry.get("name", "")
    pairs = _value_pairs(entry)
    members = ()
    for path in MEMBER_PATHS:
        found = entry.findall(path)
        if found:
            members = tuple((m.text or "").strip() for m in found)
            break
    return ObjectRecord(
        scope=scope,
        scope_type=scope_type,
        object_type=rel,
        type_label=label,
        recognized=recognized,
        name=name,
        value=" | ".join(f"{p}={t}" if t else p for p, t in pairs),
        value_sha256=hashlib.sha256(
            json.dumps(pairs, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        members=members,
        description=(entry.findtext("description") or "").strip(),
        tags=tuple((m.text or "").strip() for m in entry.findall("tag/member")),
        disable_override=(entry.findtext("disable-override") or "").strip(),
        xpath=f"{base}/{rel}/entry[@name={quote(name)}]",
        source_line=entry.sourceline or 0,
    )


def _value_pairs(entry) -> list:
    """Folhas da entry como (caminho relativo, texto), na ordem do XML, sem metadados.

    Atributos entram no caminho (ex.: match-list/entry[@name='x']/filter) para
    que entradas internas não percam a identificação.
    """
    pairs = []
    for child in entry:
        if isinstance(child.tag, str) and child.tag not in METADATA_CHILDREN:
            _walk(child, "", pairs)
    return pairs


def _walk(el, prefix: str, pairs: list) -> None:
    step = el.tag + "".join(f"[@{k}={quote(v)}]" for k, v in sorted(el.attrib.items()))
    path = f"{prefix}/{step}" if prefix else step
    children = [c for c in el if isinstance(c.tag, str)]
    if not children:
        pairs.append((path, (el.text or "").strip()))
        return
    for c in children:
        _walk(c, path, pairs)


def _check_names(scope, rel, entries, warnings) -> None:
    names = Counter(e.get("name") for e in entries)
    for name, count in names.items():
        if name is None or name == "":
            warnings.append(f"Escopo {scope}: {count} entrada(s) sem nome em {rel}")
        elif count > 1:
            warnings.append(f"Escopo {scope}: nome {name!r} repetido {count}x em {rel}")


def quote(value: str) -> str:
    """Literal XPath 1.0 seguro para qualquer nome."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"
