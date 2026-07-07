"""Entry point for ``python -m aura``.

Keeping the launcher this thin lets the real CLI code live in
:mod:`aura.cli` and stay importable by tests (e.g. the lockdown suite
imports :func:`bootstrap`) without triggering ``if __name__ == '__main__'``
side effects.
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if __name__ == "__main__":
    try:
        from aura.cli import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        print(f"[AURA] Startup error: {_exc}", file=sys.stderr)
        print("       Check config.yaml — run `cp config.example.yaml config.yaml` to reset.", file=sys.stderr)
        raise SystemExit(1)
