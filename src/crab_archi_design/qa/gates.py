from __future__ import annotations


def gate_status(gates: dict[str, bool]) -> str:
    return "pass" if gates and all(gates.values()) else "review_required"
