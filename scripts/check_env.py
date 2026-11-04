"""Valida Python e dependências. Uso: .venv/bin/python scripts/check_env.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panorama_cleanup.environment import check_environment  # noqa: E402


def main() -> int:
    checks = check_environment(ROOT / "requirements.txt")
    for c in checks:
        status = "OK  " if c.ok else "FALHA"
        note = f"  ! {c.warning}" if c.warning else ""
        print(f"{status} {c.name:<12} requerido {c.required:<8} encontrado {c.found}{note}")
    failed = [c for c in checks if not c.ok]
    print("\nAmbiente válido." if not failed else f"\n{len(failed)} verificação(ões) falharam.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
