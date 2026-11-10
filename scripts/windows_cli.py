"""Entry point for the portable Windows command-line executable."""
import os
from pathlib import Path

from panorama_cleanup.cli import main


if __name__ == "__main__":
    os.environ.setdefault("PANORAMA_CLEANUP_HOME", str(Path.cwd()))
    raise SystemExit(main())
