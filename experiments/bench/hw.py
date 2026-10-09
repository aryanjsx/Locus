"""Hardware fingerprint attached to every result, so numbers can be compared fairly."""

from __future__ import annotations

import platform
from typing import Any

import psutil


def fingerprint() -> dict[str, Any]:
    info: dict[str, Any] = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "ram_available_gb": round(psutil.virtual_memory().available / 1e9, 1),
    }
    try:
        import cpuinfo  # py-cpuinfo; slow (~1 s) but gives the CPU name and flags

        cpu = cpuinfo.get_cpu_info()
        info["cpu"] = cpu.get("brand_raw", "unknown")
        info["avx2"] = "avx2" in cpu.get("flags", [])
    except Exception as exc:  # the fingerprint must never break a run
        info["cpu"] = platform.processor() or "unknown"
        info["avx2"] = None
        info["cpu_error"] = str(exc)
    return info


def tier_for(ram_total_gb: float) -> str:
    """Hardware tier from the scope doc: below ~12 GB counts as the 8 GB tier."""
    return "8gb" if ram_total_gb < 12 else "16gb"


if __name__ == "__main__":
    import json

    fp = fingerprint()
    fp["tier"] = tier_for(fp["ram_total_gb"])
    print(json.dumps(fp, indent=2))
