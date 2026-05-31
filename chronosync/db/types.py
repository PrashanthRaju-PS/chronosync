"""Domain enums used across schema and DTOs."""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    BOND = "BOND"
    FX = "FX"
    CRYPTO = "CRYPTO"
