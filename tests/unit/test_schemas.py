"""Response-schema serialization guards."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from chronosync.api.schemas import InstrumentDTO


def _dto(market_cap: Decimal | None) -> InstrumentDTO:
    return InstrumentDTO(
        id=uuid4(),
        ticker="FOO",
        exchange="NSE",
        asset_class="EQUITY",
        country_code="IN",
        currency="INR",
        market_cap=market_cap,
        market_cap_as_of=date(2026, 9, 5),
        is_active=True,
    )


@pytest.mark.unit
@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_market_cap_coerced_to_none(bad: Decimal) -> None:
    """A non-finite Decimal is a valid Decimal but has no JSON encoding — left as
    is it 500s the whole /instruments list. The DTO coerces it to null so one bad
    row degrades to a missing field, and the model still serializes to JSON."""
    dto = _dto(bad)
    assert dto.market_cap is None
    dto.model_dump_json()  # must not raise


@pytest.mark.unit
def test_finite_market_cap_preserved() -> None:
    dto = _dto(Decimal("1500000.00"))
    assert dto.market_cap == Decimal("1500000.00")


@pytest.mark.unit
def test_null_market_cap_preserved() -> None:
    assert _dto(None).market_cap is None
