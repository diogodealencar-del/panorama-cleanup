"""Nome de objetos renomeados por conflito (CLAUDE.md §6, regra 2 do usuário).

Objeto com o mesmo nome de outro e valor diferente recebe, ao lado do nome, o
próprio valor:

- address ip-netmask de host (/32 ou /128, ou sem máscara): NOME-IP
  (SERVER-192.168.10.10);
- address ip-netmask de rede: NOME-REDE-MÁSCARA (NET-10.0.0.0-24); a barra não é
  permitida em nome;
- address ip-range: NOME-INICIO-FIM;
- address fqdn: NOME-FQDN (WEB-portal.empresa.com.br);
- address ip-wildcard: NOME-ENDEREÇO-MÁSCARA;
- service: NOME-PROTOCOLO-PORTA (Antivirus-tcp-8764); vírgulas viram "_";
- grupos e demais tipos: NOME_DEVICEGROUP (sem valor único que os identifique).

O nome é determinístico. Se o resultado tiver caractere fora do conjunto aceito
(letras, números, espaço, hífen, underscore e ponto) ou passar do limite de
caracteres, nada é truncado: o planejador marca o objeto como PENDING_DECISION
para nome manual.
"""

from __future__ import annotations

import ipaddress
import re

VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")


def value_suffix(object_type: str, element) -> str:
    """Sufixo de valor do objeto, ou "" quando o tipo não tem valor único."""
    if object_type == "address":
        return _address_suffix(element)
    if object_type == "service":
        return _service_suffix(element)
    return ""


def _address_suffix(el) -> str:
    text = (el.findtext("ip-netmask") or "").strip()
    if text:
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError:
            return text.replace("/", "-")
        address = text.split("/", 1)[0]
        if net.prefixlen == net.max_prefixlen:
            return address
        return f"{address}-{net.prefixlen}"
    for kind in ("ip-range", "fqdn"):
        text = (el.findtext(kind) or "").strip()
        if text:
            return text
    text = (el.findtext("ip-wildcard") or "").strip()
    if text:
        return text.replace("/", "-")
    return ""


def _service_suffix(el) -> str:
    protocols = [p for p in el.findall("protocol/*") if isinstance(p.tag, str)]
    if len(protocols) != 1:
        return ""
    proto = protocols[0]
    port = "".join((proto.findtext("port") or "").split())
    if not port:
        return ""
    return f"{proto.tag}-{port.replace(',', '_')}"


def conflict_name(name: str, object_type: str, element, scope: str) -> tuple:
    """(novo nome, regra aplicada)."""
    suffix = value_suffix(object_type, element)
    if suffix:
        return f"{name}-{suffix}", "valor"
    return f"{name}_{scope}", "device-group"


def valid_name(name: str) -> bool:
    return bool(VALID_NAME.match(name))
