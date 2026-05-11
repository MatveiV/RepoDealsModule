-- ============================================================
-- REPO Module: SQLite Schema Migration (Demo Mode)
-- Version: 1.0.0
--
-- Adaptations from PostgreSQL:
--   - DECIMAL(20,6) → REAL (precision controlled via ROUND() in app)
--   - UUID → TEXT (generated in application layer)
--   - JSONB → TEXT (JSON stored as text)
--   - BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT
--   - Partitioning → single table with TTL cleanup
--   - CHECK constraints → BEFORE INSERT/UPDATE triggers
--   - gen_random_uuid() → application-side UUID generation
-- ============================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─── Instruments ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id       TEXT PRIMARY KEY,
    short_name          TEXT NOT NULL,
    instrument_type     TEXT NOT NULL,
    currency            TEXT NOT NULL,
    repo_eligible       INTEGER NOT NULL DEFAULT 1,  -- 0/1 boolean
    settlement_mode     TEXT NOT NULL DEFAULT 'T+0',
    day_count_convention TEXT NOT NULL DEFAULT 'ACT/365',
    is_active           INTEGER NOT NULL DEFAULT 1,
    valid_from          TEXT NOT NULL,  -- ISO date string
    valid_to            TEXT
);

-- ─── Participants ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS participants (
    participant_id  TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

-- ─── Raw Trades (no partitioning; TTL cleanup via scheduled job) ─
CREATE TABLE IF NOT EXISTS raw_trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key     TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'BATCH',
    payload             TEXT NOT NULL,  -- JSON stored as text
    received_at         TEXT NOT NULL DEFAULT (datetime('now')),
    processing_status   TEXT NOT NULL DEFAULT 'PENDING',
    error_message       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_trades_ikey
    ON raw_trades (idempotency_key, received_at);

-- ─── Trades ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    trade_id                TEXT PRIMARY KEY,  -- UUID as TEXT
    external_trade_id       TEXT NOT NULL,
    idempotency_key         TEXT NOT NULL,
    raw_trade_id            INTEGER REFERENCES raw_trades(id) ON DELETE SET NULL,
    party_1_id              TEXT NOT NULL,
    party_2_id              TEXT NOT NULL,
    initiator_role          TEXT NOT NULL,
    participant_id          TEXT NOT NULL,
    counterparty_id         TEXT NOT NULL,
    instrument_id           TEXT NOT NULL REFERENCES instruments(instrument_id),
    quantity                REAL NOT NULL,
    leg1_amount             REAL NOT NULL,
    leg2_amount             REAL,
    rate                    REAL NOT NULL,
    trade_date              TEXT NOT NULL,  -- ISO date
    leg1_settlement_date    TEXT NOT NULL,
    leg2_settlement_date    TEXT NOT NULL,
    days_to_maturity        INTEGER NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'NEW',
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_ikey ON trades (idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_ext ON trades (external_trade_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_participant ON trades (participant_id, status);
CREATE INDEX IF NOT EXISTS idx_trades_leg2 ON trades (leg2_settlement_date, status);

-- ─── Positions ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    position_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id  TEXT NOT NULL,
    instrument_id   TEXT NOT NULL,
    balance_type    TEXT NOT NULL,
    currency        TEXT NOT NULL,
    position_date   TEXT NOT NULL,
    balance         REAL NOT NULL DEFAULT 0,
    frozen_balance  REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    inconsistent    INTEGER NOT NULL DEFAULT 0,
    calculated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (participant_id, instrument_id, balance_type, position_date, status)
);

CREATE INDEX IF NOT EXISTS idx_positions_lookup
    ON positions (participant_id, position_date, status);

-- Trigger: emulate CHECK (balance >= 0) on INSERT
CREATE TRIGGER IF NOT EXISTS trg_positions_balance_insert
BEFORE INSERT ON positions
FOR EACH ROW
WHEN NEW.balance < 0
BEGIN
    SELECT RAISE(ABORT, 'CHECK constraint failed: chk_positive_balance');
END;

-- Trigger: emulate CHECK (balance >= 0) on UPDATE
CREATE TRIGGER IF NOT EXISTS trg_positions_balance_update
BEFORE UPDATE ON positions
FOR EACH ROW
WHEN NEW.balance < 0
BEGIN
    SELECT RAISE(ABORT, 'CHECK constraint failed: chk_positive_balance');
END;

-- ─── Future Obligations ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS future_obligations (
    obligation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT NOT NULL REFERENCES trades(trade_id),
    participant_id  TEXT NOT NULL,
    instrument_id   TEXT NOT NULL,
    balance_type    TEXT NOT NULL,
    obligation_type TEXT NOT NULL,
    obligation_date TEXT NOT NULL,
    quantity        REAL,
    amount          REAL,
    currency        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_oblig_date ON future_obligations (obligation_date, status);
CREATE INDEX IF NOT EXISTS idx_oblig_part ON future_obligations (participant_id, obligation_date);

-- ─── Position Audit Log ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS position_audit_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER REFERENCES positions(position_id),
    participant_id  TEXT NOT NULL,
    instrument_id   TEXT NOT NULL,
    balance_type    TEXT NOT NULL,
    position_date   TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    delta           REAL NOT NULL,
    balance_before  REAL NOT NULL,
    balance_after   REAL NOT NULL,
    trade_id        TEXT,
    chunk_id        INTEGER,
    operator        TEXT,
    prev_log_hash   TEXT,
    logged_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── Rejected Trades ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rejected_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT,
    raw_trade_id    INTEGER REFERENCES raw_trades(id) ON DELETE SET NULL,
    rejection_type  TEXT NOT NULL,
    rejection_detail TEXT NOT NULL,
    payload         TEXT NOT NULL,  -- JSON as text
    chunk_id        INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT,
    resolved_by     TEXT
);

-- ─── Load Reports ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS load_reports (
    report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    eod_date        TEXT NOT NULL,
    file_name       TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total_records   INTEGER NOT NULL DEFAULT 0,
    committed       INTEGER NOT NULL DEFAULT 0,
    rejected        INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    failed_chunks   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    UNIQUE (eod_date)
);
