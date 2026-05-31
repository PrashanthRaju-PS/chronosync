# Original Prompt (verbatim)

> Preserved for traceability. The authoritative spec is `Requirements.md` (root) and the design documents in this folder.

---

Act as a Principal Data Engineer and Systems Architect. Design and write a production-grade Python standalone background daemon that manages an efficient, scalable historical Price-Volume database. The system must be built with a modular, provider-agnostic architecture, starting with Indian Equities (NSE/BSE), but architected to scale to global markets, futures, options, and fixed-income assets.

### 1. Architectural Requirements
*   **Daemon Design:** Must run continuously as a background process (or via a robust crontab/systemd service worker paradigm) utilizing asynchronous execution (`asyncio`) or multi-threading for optimized parallel data fetching.
*   **Database Engine:** Implement a storage layer optimized for time-series data. Use PostgreSQL with TimescaleDB (preferred) or a highly indexed SQLite/DuckDB setup for local standalone efficiency.
*   **Data Schema (Modular & Extensible):**
    *   `instruments` table: `id` (UUID), `ticker`, `exchange`, `asset_class` (Equity, Future, Bond), `country_code`, `market_cpaital`,`currency`, `is_active`.
    *   `daily_bars` table (Hypertable/Partitioned): `instrument_id`, `timestamp` (Date), `open`, `high`, `low`, `close`, `volume`, `vwap`, `open_interest` (nullable).
*   **Idempotency & Resilience:** Implement strict duplicate prevention (upsert/ON CONFLICT DO UPDATE). Handle network drops, rate limits (HTTP 429), and API timeouts gracefully using exponential backoff retry logic.

### 2. Functional Requirements & Sync Logic
*   **Reliable Data Sourcing:** Build a clean data abstraction layer (`BaseDataFeed` interface). Implement concrete providers for Indian markets using reliable open-source or commercial APIs (e.g., `yfinance` as a base fallback, `Brezzy/NseTools` or web scrapers targeting official exchange files like Bhavcopy, or commercial hooks like Kite Connect/Punch).
*   **Sync Sequence:**
    1.  At startup, scan the `instruments` universe and cross-reference the database to find the latest `max(timestamp)` for each active stock.
    2.  Calculate the missing date range (delta from last sync to current EOD).
    3.  Fetch daily historical candles in optimized batches.
    4.  Validate corporate actions adjustments (Ensure OHLC data handles historical splits/bonuses gracefully if the provider supports adjusted data).
*   **Logging & State Management:** Comprehensive logging using standard Python `logging` with file rotation. Maintain a sync state log table to track pipeline health and partial failures.

### Your Task:
Generate the entire folder structure and the complete, well-documented Python code for this daemon. Break the code down into clean modules: `config.py`, `database.py`, `providers.py` (abstract class + concrete NSE implementation), and `daemon.py` (the core execution engine loop). Prioritize clean code, type hinting, and strict memory efficiency when handling thousands of tickers.
