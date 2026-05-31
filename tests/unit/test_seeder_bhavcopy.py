"""Bhavcopy parser tests using synthetic ZIP fixtures."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest

from chronosync.seeders.bse_bhavcopy import _parse_zip_csv as bse_parse
from chronosync.seeders.nse_bhavcopy import _parse_zip_csv as nse_parse


def _zip_csv(name: str, header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        csv_buf = io.StringIO()
        w = csv.writer(csv_buf)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
        zf.writestr(name, csv_buf.getvalue())
    return buf.getvalue()


@pytest.mark.unit
def test_nse_parser_filters_series() -> None:
    content = _zip_csv(
        "BhavCopy_NSE_CM_0_0_0_20260101_F_0000.csv",
        ["TckrSymb", "SctySrs", "ISIN"],
        [
            ["RELIANCE", "EQ", "INE002A01018"],
            ["RELIANCEBL", "BL", "INE999999999"],   # filtered out
            ["TCS", "EQ", "INE467B01029"],
        ],
    )
    rows = nse_parse(content)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"RELIANCE", "TCS"}
    assert all(r["exchange"] == "NSE" for r in rows)
    assert all(r["currency"] == "INR" for r in rows)


@pytest.mark.unit
def test_nse_parser_legacy_headers() -> None:
    content = _zip_csv(
        "cm01JAN2024bhav.csv",
        ["SYMBOL", "SERIES", "ISIN"],
        [["INFY", "EQ", "INE009A01021"]],
    )
    rows = nse_parse(content)
    assert rows == [
        {
            "ticker": "INFY",
            "exchange": "NSE",
            "asset_class": "EQUITY",
            "country_code": "IN",
            "currency": "INR",
            "isin": "INE009A01021",
            "is_active": True,
        }
    ]


@pytest.mark.unit
def test_bse_parser() -> None:
    content = _zip_csv(
        "EQ_ISINCODE_010126.CSV",
        ["SC_CODE", "SC_NAME", "ISIN_CODE"],
        [
            ["500325", "RELIANCE", "INE002A01018"],
            ["", "GARBAGE", "X"],   # ignored — empty code
        ],
    )
    rows = bse_parse(content)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "500325"
    assert rows[0]["exchange"] == "BSE"
