"""Leitura segura do backup XML do Panorama e extração de metadados e hierarquia.

Somente leitura: o arquivo é aberto para parse e nunca regravado.
"""

from __future__ import annotations

import xml.etree.ElementTree as StdET
from dataclasses import dataclass, field
from pathlib import Path

import defusedxml.ElementTree as DefusedET
from defusedxml import DefusedXmlException
from lxml import etree

from panorama_cleanup.errors import XmlReadError

SHARED = "shared"
UNKNOWN = "UNKNOWN"


def _parser() -> etree.XMLParser:
    # Sem entidades, DTD ou rede; preserva espaços, comentários e PIs (CLAUDE.md §8).
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        remove_blank_text=False,
        remove_comments=False,
        remove_pis=False,
        huge_tree=False,
    )


def parse_backup(path: Path) -> etree._ElementTree:
    """Valida com defusedxml (recusa DTD/entidades) e então faz o parse com lxml."""
    path = Path(path)
    try:
        DefusedET.parse(str(path), forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise XmlReadError(f"XML recusado por construção insegura ({type(exc).__name__}): {path}")
    except StdET.ParseError as exc:
        raise XmlReadError(f"XML malformado: {path}: {exc}")

    try:
        tree = etree.parse(str(path), _parser())
    except etree.XMLSyntaxError as exc:
        raise XmlReadError(f"XML malformado: {path}: {exc}")

    root = tree.getroot()
    if root.tag != "config":
        raise XmlReadError(f"Raiz inesperada <{root.tag}>; esperado <config> do Panorama")
    return tree


@dataclass(frozen=True)
class PanoramaMetadata:
    config_version: str
    detail_version: str
    urldb: str
    panorama_entry: str
    managed_devices: tuple
    templates: tuple
    template_stacks: tuple


@dataclass
class DeviceGroup:
    name: str
    parent: str
    parent_source: str
    readonly_id: str
    devices: tuple
    reference_templates: tuple
    depth: int = 0
    path: tuple = field(default_factory=tuple)


def panorama_entry(root: etree._Element) -> etree._Element:
    entries = root.findall("devices/entry")
    if len(entries) != 1:
        raise XmlReadError(
            f"Esperado exatamente um /config/devices/entry, encontrados {len(entries)}"
        )
    return entries[0]


def read_metadata(tree: etree._ElementTree) -> PanoramaMetadata:
    root = tree.getroot()
    if not root.get("version"):
        raise XmlReadError("Atributo version ausente em <config>; versão não identificável")
    device = panorama_entry(root)
    return PanoramaMetadata(
        config_version=root.get("version"),
        detail_version=root.get("detail-version", ""),
        urldb=root.get("urldb", ""),
        panorama_entry=device.get("name", ""),
        managed_devices=tuple(e.get("name") for e in root.findall("mgt-config/devices/entry")),
        templates=tuple(e.get("name") for e in device.findall("template/entry")),
        template_stacks=tuple(e.get("name") for e in device.findall("template-stack/entry")),
    )


def read_device_groups(tree: etree._ElementTree) -> tuple:
    """Lê os Device Groups e resolve a hierarquia a partir de /config/readonly.

    No PAN-OS a hierarquia fica em readonly/devices/entry/device-group/entry:
    <parent-dg> indica o pai; sua ausência indica filho direto de Shared.
    DG sem entrada em readonly, pai inexistente ou ciclo resultam em pai UNKNOWN.
    Retorna (lista de DeviceGroup, lista de avisos).
    """
    root = tree.getroot()
    device = panorama_entry(root)
    warnings = []

    readonly = {}
    for entry in root.findall(
        f"readonly/devices/entry[@name='{device.get('name')}']/device-group/entry"
    ):
        readonly[entry.get("name")] = entry

    groups = {}
    for entry in device.findall("device-group/entry"):
        name = entry.get("name")
        ro = readonly.get(name)
        if ro is None:
            parent, source, ro_id = UNKNOWN, "ausente em readonly", ""
            warnings.append(f"Device Group {name}: sem entrada em readonly; hierarquia desconhecida")
        else:
            parent_el = ro.find("parent-dg")
            ro_id = (ro.findtext("id") or "").strip()
            if parent_el is not None and (parent_el.text or "").strip():
                parent, source = parent_el.text.strip(), "readonly/parent-dg"
            else:
                parent, source = SHARED, "readonly sem parent-dg"
        groups[name] = DeviceGroup(
            name=name,
            parent=parent,
            parent_source=source,
            readonly_id=ro_id,
            devices=tuple(d.get("name") for d in entry.findall("devices/entry")),
            reference_templates=tuple(
                (m.text or "").strip() for m in entry.findall("reference-templates/member")
            ),
        )

    for name in sorted(set(readonly) - set(groups)):
        warnings.append(f"Device Group {name}: presente em readonly mas ausente na configuração")

    for dg in groups.values():
        if dg.parent not in (SHARED, UNKNOWN) and dg.parent not in groups:
            warnings.append(f"Device Group {dg.name}: pai {dg.parent!r} inexistente")
            dg.parent = UNKNOWN

    # Ciclos são detectados na hierarquia original, antes de qualquer marcação,
    # para que todos os DGs do ciclo (e abaixo dele) fiquem UNKNOWN.
    declared = {name: dg.parent for name, dg in groups.items()}
    for dg in groups.values():
        chain, current = [dg.name], declared[dg.name]
        while current not in (SHARED, UNKNOWN):
            if current in chain:
                warnings.append(f"Device Group {dg.name}: ciclo na hierarquia {chain + [current]}")
                dg.parent = UNKNOWN
                break
            chain.append(current)
            current = declared[current]

    for dg in groups.values():
        chain, current = [dg.name], dg.parent
        while current not in (SHARED, UNKNOWN):
            chain.append(current)
            current = groups[current].parent
        dg.path = (current,) + tuple(reversed(chain))
        dg.depth = len(dg.path) - 1

    return list(groups.values()), warnings
