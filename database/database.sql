CREATE TABLE pix_analyses (
    id BIGSERIAL PRIMARY KEY,
    analysis_key UUID NOT NULL UNIQUE,
    pix_id VARCHAR(128) NOT NULL UNIQUE,
    payload_hash CHAR(64) NOT NULL,

    amount BIGINT NOT NULL,
    transaction_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    source_document VARCHAR(14) NOT NULL,
    destination_document VARCHAR(14) NOT NULL,
    device_id VARCHAR(128) NOT NULL,
    source_ip INET NOT NULL,
    channel VARCHAR(32) NOT NULL,

    status VARCHAR(24) NOT NULL,
    policy_version VARCHAR(32) NOT NULL,

    raw_payload JSONB NOT NULL,
    decision_metadata JSONB NOT NULL,

    CONSTRAINT pix_analyses_amount_check
        CHECK (amount > 0),

    CONSTRAINT pix_analyses_status_check
        CHECK (
            status IN (
                'approved',
                'two_factor_required',
                'rejected'
            )
        ),

    CONSTRAINT pix_analyses_pix_id_check
        CHECK (LENGTH(BTRIM(pix_id)) > 0),

    CONSTRAINT pix_analyses_policy_version_check
        CHECK (LENGTH(BTRIM(policy_version)) > 0),

    CONSTRAINT pix_analyses_raw_payload_check
        CHECK (jsonb_typeof(raw_payload) = 'object'),

    CONSTRAINT pix_analyses_decision_metadata_check
        CHECK (jsonb_typeof(decision_metadata) = 'object')
);

CREATE INDEX pix_analyses_source_history_idx
    ON pix_analyses (source_document, transaction_at, id);

CREATE INDEX pix_analyses_destination_history_idx
    ON pix_analyses (destination_document, transaction_at, id);

CREATE INDEX pix_analyses_source_activity_idx
    ON pix_analyses (source_document, received_at, id);

CREATE INDEX pix_analyses_destination_activity_idx
    ON pix_analyses (destination_document, received_at, id);

CREATE INDEX pix_analyses_device_activity_idx
    ON pix_analyses (device_id, received_at, id);

CREATE INDEX pix_analyses_device_context_idx
    ON pix_analyses (device_id, received_at)
    INCLUDE (source_document, status, amount, pix_id);