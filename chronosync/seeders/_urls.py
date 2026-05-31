"""Bhavcopy URL templates. Override via env if NSE/BSE change archive paths.

NSE Bhavcopy archive URLs change periodically; the templates below are best-known
as of v1. Operators can override via CHRONOSYNC_SEEDER_NSE_URL / _BSE_URL env vars.
"""

from __future__ import annotations

import os
from datetime import date

NSE_BHAVCOPY_URL_DEFAULT = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
BSE_BHAVCOPY_URL_DEFAULT = (
    "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_{ddmmyy}.zip"
)


def nse_url(d: date) -> str:
    tmpl = os.environ.get("CHRONOSYNC_SEEDER_NSE_URL", NSE_BHAVCOPY_URL_DEFAULT)
    return tmpl.format(
        yyyymmdd=d.strftime("%Y%m%d"),
        yyyy=d.year,
        mm=f"{d.month:02d}",
        dd=f"{d.day:02d}",
        ddmmyy=d.strftime("%d%m%y"),
        ddmmyyyy=d.strftime("%d%m%Y"),
    )


def bse_url(d: date) -> str:
    tmpl = os.environ.get("CHRONOSYNC_SEEDER_BSE_URL", BSE_BHAVCOPY_URL_DEFAULT)
    return tmpl.format(
        yyyymmdd=d.strftime("%Y%m%d"),
        yyyy=d.year,
        mm=f"{d.month:02d}",
        dd=f"{d.day:02d}",
        ddmmyy=d.strftime("%d%m%y"),
        ddmmyyyy=d.strftime("%d%m%Y"),
    )
