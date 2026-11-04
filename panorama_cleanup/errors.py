"""Exceções da ferramenta."""


class PanoramaCleanupError(Exception):
    """Erro base da ferramenta."""


class ConfigError(PanoramaCleanupError):
    """Configuração inválida ou incompleta."""


class SafetyViolation(PanoramaCleanupError):
    """Operação recusada por violar uma regra de preservação (CLAUDE.md §8 e §14)."""


class XmlReadError(PanoramaCleanupError):
    """Backup XML ilegível, inseguro ou com estrutura inesperada."""


class AnalysisError(PanoramaCleanupError):
    """Análise interrompida por falta de evidência para resolver referências."""
