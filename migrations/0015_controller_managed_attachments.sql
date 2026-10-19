-- Instance Consumer v2 owns a monotonic workload snapshot, independently of
-- attachment leases and versions. Existing single_pod records remain untouched.
CREATE TABLE network_attachment_consumer_snapshots (
 tenant_id uuid NOT NULL,
 attachment_id text NOT NULL,
 snapshot_revision bigint NOT NULL DEFAULT 0 CHECK(snapshot_revision>=0),
 snapshot_hash text NOT NULL DEFAULT '' CHECK(snapshot_hash='' OR snapshot_hash ~ '^[0-9a-f]{64}$'),
 root_controller jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(root_controller)='object'),
 snapshot jsonb CHECK(snapshot IS NULL OR jsonb_typeof(snapshot)='object'),
 confirmed_snapshot_revision bigint NOT NULL DEFAULT 0 CHECK(confirmed_snapshot_revision>=0),
 confirmed_pod_uid text NOT NULL DEFAULT '',
 PRIMARY KEY(tenant_id,attachment_id),
 FOREIGN KEY(tenant_id,attachment_id) REFERENCES network_attachments(tenant_id,attachment_id)
);
