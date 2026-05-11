-- ============================================================
-- REPO Module: PostgreSQL 15+ Initial Schema Migration
-- Version: 1.0.0
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Instruments ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id       VARCHAR(32) PRIMARY KEY,
    short_name          VARCHAR(64) NOT NULL,
    instrument_type     VARCHAR(16) NOT NULL,
    currency            CHAR(3) NOT NULL,
    repo_eligible       BOOLEAN NOT NULL DEFAULT TRUE,
    settlement_mode     VARCHAR(4) NOT NULL DEFAULT 'T+0',
    day_count_convention VARCHAR(16) NOT NULL DEFAULT 'ACT/365',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          DATE NOT NULL,
    valid_to            DATE
);

COMMENT ON TABLE instruments IS 'Reference data: tradeable instruments. Master: НКЦ/депозитарий. Replicated daily before 21:30 MSK.';

-- ─── Participants ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS participants (
    participant_id  VARCHAR(32) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ─── Raw Trades (append-only, partitioned by received_at) ────
CREATE TABLE IF NOT EXISTS raw_trades (
    id                  BIGSERIAL,
    idempotency_key     VARCHAR(128) NOT NULL,
    source              VARCHAR(32) NOT NULL DEFAULT 'BATCH',
    payload             JSONB NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_status   VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    error_message       TEXT,
    PRIMARY KEY (id, received_at)
) PARTITION BY RANGE (received_at);

-- Monthly partitions (create for current + next 3 months)
CREATE TABLE IF NOT EXISTS raw_trades_2026_05
    PARTITION OF raw_trades
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS raw_trades_2026_06
    PARTITION OF raw_trades
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS raw_trades_2026_07
    PARTITION OF raw_trades
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE IF NOT EXISTS raw_trades_2026_08
    PARTITION OF raw_trades
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- Dedup index (30-day window)
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_trades_ikey
    ON raw_trades (idempotency_key, received_at);

-- ─── Trades ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    trade_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_trade_id       VARCHAR(64) NOT NULL,
    idempotency_key         VARCHAR(128) NOT NULL,
    raw_trade_id            BIGINT,  -- FK to raw_trades.id (no FK constraint due to partitioning)
    party_1_id              VARCHAR(32) NOT NULL,
    party_2_id              VARCHAR(32) NOT NULL,
    initiator_role          VARCHAR(20) NOT NULL
                                CHECK (initiator_role IN ('SECURITY_SELLER','SECURITY_BUYER','DEFAULT_SELLER')),
    participant_id          VARCHAR(32) NOT NULL,
    counterparty_id         VARCHAR(32) NOT NULL,
    instrument_id           VARCHAR(32) NOT NULL REFERENCES instruments(instrument_id),
    quantity                DECIMAL(20,6) NOT NULL,
    leg1_amount             DECIMAL(20,2) NOT NULL,
    leg2_amount             DECIMAL(20,2),
    rate                    DECIMAL(10,6) NOT NULL,
    trade_date              DATE NOT NULL,
    leg1_settlement_date    DATE NOT NULL,
    leg2_settlement_date    DATE NOT NULL,
    days_to_maturity        INTEGER NOT NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'NEW'
                                CHECK (status IN ('NEW','ACTIVE','CLOSED','CANCELLED','REJECTED')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_ikey ON trades (idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_ext ON trades (external_trade_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_participant ON trades (participant_id, status);
CREATE INDEX IF NOT EXISTS idx_trades_leg2 ON trades (leg2_settlement_date, status);

-- ─── Positions ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    position_id     BIGSERIAL PRIMARY KEY,
    participant_id  VARCHAR(32) NOT NULL,
    instrument_id   VARCHAR(32) NOT NULL,
    -- SECURITIES: ISIN/code; CASH: 'CASH_{currency}' e.g. CASH_RUB
    balance_type    VARCHAR(12) NOT NULL
                        CHECK (balance_type IN ('SECURITIES','CASH')),
    currency        CHAR(3) NOT NULL,
    position_date   DATE NOT NULL,
    balance         DECIMAL(20,6) NOT NULL DEFAULT 0
                        CONSTRAINT chk_positive_balance CHECK (balance >= 0),
    frozen_balance  DECIMAL(20,6) NOT NULL DEFAULT 0,
    status          VARCHAR(8) NOT NULL CHECK (status IN ('SOD','EOD')),
    inconsistent    BOOLEAN NOT NULL DEFAULT FALSE,
    calculated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (participant_id, instrument_id, balance_type, position_date, status)
);

CREATE INDEX IF NOT EXISTS idx_positions_lookup
    ON positions (participant_id, position_date, status);

COMMENT ON COLUMN positions.balance IS
    'CHECK (balance >= 0) is the last line of defense. Application pre-check (§3.3.4) is the first.';

-- ─── Future Obligations ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS future_obligations (
    obligation_id   BIGSERIAL PRIMARY KEY,
    trade_id        UUID NOT NULL REFERENCES trades(trade_id),
    participant_id  VARCHAR(32) NOT NULL,
    instrument_id   VARCHAR(32) NOT NULL,
    balance_type    VARCHAR(12) NOT NULL,
    obligation_type VARCHAR(32) NOT NULL,
    obligation_date DATE NOT NULL,
    quantity        DECIMAL(20,6),
    amount          DECIMAL(20,2),
    currency        CHAR(3) NOT NULL,
    status          VARCHAR(12) NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','FULFILLED','CANCELLED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_oblig_date ON future_obligations (obligation_date, status);
CREATE INDEX IF NOT EXISTS idx_oblig_part ON future_obligations (participant_id, obligation_date);

-- ─── Position Audit Log (append-only, hash-chain) ─────────────
CREATE TABLE IF NOT EXISTS position_audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    position_id     BIGINT REFERENCES positions(position_id),
    participant_id  VARCHAR(32) NOT NULL,
    instrument_id   VARCHAR(32) NOT NULL,
    balance_type    VARCHAR(12) NOT NULL,
    position_date   DATE NOT NULL,
    change_type     VARCHAR(32) NOT NULL,
    delta           DECIMAL(20,6) NOT NULL,
    balance_before  DECIMAL(20,6) NOT NULL,
    balance_after   DECIMAL(20,6) NOT NULL,
    trade_id        UUID,
    chunk_id        INTEGER,
    operator        VARCHAR(64),
    prev_log_hash   VARCHAR(64),  -- SHA-256 hash chain
    logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE position_audit_log IS 'Append-only audit log with SHA-256 hash chain. Role audit_writer: INSERT + SELECT only.';

-- ─── Rejected Trades ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rejected_trades (
    id              BIGSERIAL PRIMARY KEY,
    idempotency_key VARCHAR(128),
    raw_trade_id    BIGINT,
    rejection_type  VARCHAR(32) NOT NULL,
    rejection_detail TEXT NOT NULL,
    payload         JSONB NOT NULL,
    chunk_id        INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     VARCHAR(64)
);

-- ─── Load Reports ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS load_reports (
    report_id       BIGSERIAL PRIMARY KEY,
    eod_date        DATE NOT NULL,
    file_name       VARCHAR(256),
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    total_records   INTEGER NOT NULL DEFAULT 0,
    committed       INTEGER NOT NULL DEFAULT 0,
    rejected        INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    failed_chunks   INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(16) NOT NULL DEFAULT 'IN_PROGRESS'
                        CHECK (status IN ('IN_PROGRESS','COMPLETED','COMPLETED_WITH_ERRORS','FAILED')),
    UNIQUE (eod_date)
);

-- ─── Audit writer role ────────────────────────────────────────
-- CREATE ROLE audit_writer;
-- GRANT INSERT, SELECT ON position_audit_log TO audit_writer;
-- GRANT INSERT, SELECT ON raw_trades TO audit_writer;
