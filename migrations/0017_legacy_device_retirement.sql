-- Legacy registration retirement owns a separate operation and executor.
-- The original resource ID, binding ID, adoption operation and histories remain.
ALTER TABLE network_device_adoptions ADD COLUMN registration_closed boolean NOT NULL DEFAULT false;

CREATE TABLE network_legacy_device_retirements (
 resource_id text PRIMARY KEY,
 cluster_id text NOT NULL,
 operation_id uuid NOT NULL UNIQUE,
 expected_version bigint NOT NULL CHECK(expected_version>0),
 fence_version bigint NOT NULL CHECK(fence_version>expected_version),
 binding_id uuid NOT NULL,
 device_name text NOT NULL,
 node_inventory jsonb NOT NULL CHECK(jsonb_typeof(node_inventory)='array'),
 pre_managed boolean NOT NULL,
 config_uid text NOT NULL DEFAULT '',
 state text NOT NULL DEFAULT 'closing' CHECK(state IN ('closing','blocked','retired')),
 reason text NOT NULL DEFAULT '',
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 pending_unknown boolean NOT NULL,
 legacy_pending_action text NOT NULL,
 executor_epoch bigint NOT NULL,
 drain_after timestamptz NOT NULL,
 prepared boolean NOT NULL DEFAULT false,
 config_rv text NOT NULL DEFAULT '',
 config_managed_raw text NOT NULL DEFAULT '',
 desired_managed_raw text NOT NULL DEFAULT '',
 marker_present boolean NOT NULL DEFAULT false,
 marker_value text NOT NULL DEFAULT '',
 lease_owner uuid,
 lease_until timestamptz,
 lease_epoch bigint NOT NULL DEFAULT 0,
 next_run_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 completed_at timestamptz,
 UNIQUE(resource_id,operation_id),
 FOREIGN KEY(cluster_id,resource_id) REFERENCES network_device_adoptions(cluster_id,resource_id),
 CHECK((state='retired')=(completed_at IS NOT NULL)),
 CHECK(state<>'retired' OR NOT pending_unknown),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL)),
 CHECK(NOT prepared OR (config_uid<>'' AND config_rv<>''))
);
CREATE INDEX network_legacy_device_retirements_due ON network_legacy_device_retirements(next_run_at) WHERE state<>'retired';

CREATE TABLE network_legacy_device_retirement_idempotency (
 cluster_id text NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
 resource_id text NOT NULL,
 operation_id uuid NOT NULL,
 response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'),
 created_at timestamptz NOT NULL,
 PRIMARY KEY(cluster_id,idempotency_key),
 FOREIGN KEY(resource_id,operation_id) REFERENCES network_legacy_device_retirements(resource_id,operation_id)
);
CREATE TABLE network_legacy_device_retirement_history (
 history_id uuid PRIMARY KEY,
 resource_id text NOT NULL,
 operation_id uuid NOT NULL,
 event text NOT NULL,
 actor_ref text NOT NULL DEFAULT '',
 caller_ref text NOT NULL DEFAULT '',
 detail jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(detail)='object'),
 created_at timestamptz NOT NULL,
 FOREIGN KEY(resource_id,operation_id) REFERENCES network_legacy_device_retirements(resource_id,operation_id)
);
