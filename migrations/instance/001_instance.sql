CREATE SCHEMA instance;
CREATE TABLE instance.schema_version (version integer PRIMARY KEY, checksum text NOT NULL);
CREATE TABLE instance.instances (
 tenant_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 create_operation_id uuid NOT NULL,
 create_request_hash text NOT NULL,
 version bigint NOT NULL CHECK(version>0),
 desired text NOT NULL CHECK(desired IN ('running','stopped','deleted')),
 snapshot jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,instance_id),
 UNIQUE(tenant_id,create_operation_id)
);
CREATE TABLE instance.instance_operations (
 tenant_id uuid NOT NULL,
 operation_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 action text NOT NULL,
 idempotency_key text NOT NULL,
 request_hash text NOT NULL,
 version bigint NOT NULL,
 ack jsonb NOT NULL,
 state text NOT NULL DEFAULT 'accepted',
 reason text NOT NULL DEFAULT '',
 external_result jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,operation_id),
 UNIQUE(tenant_id,instance_id,action,idempotency_key),
 FOREIGN KEY(tenant_id,instance_id) REFERENCES instance.instances(tenant_id,instance_id)
);
CREATE TABLE instance.instance_work (
 tenant_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 due_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_owner text NOT NULL DEFAULT '',
 lease_epoch bigint NOT NULL DEFAULT 0,
 lease_until timestamptz NOT NULL DEFAULT '-infinity',
 PRIMARY KEY(tenant_id,instance_id),
 FOREIGN KEY(tenant_id,instance_id) REFERENCES instance.instances(tenant_id,instance_id)
);
CREATE INDEX instance_work_due ON instance.instance_work(due_at,lease_until);
CREATE TABLE instance.workspace_bindings (
 tenant_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 namespace text NOT NULL,
 pvc_name text NOT NULL,
 pvc_uid text NOT NULL DEFAULT '',
 cleanup_state text NOT NULL DEFAULT 'pending',
 PRIMARY KEY(tenant_id,instance_id),
 UNIQUE(tenant_id,namespace,pvc_name),
 FOREIGN KEY(tenant_id,instance_id) REFERENCES instance.instances(tenant_id,instance_id)
);
CREATE TABLE instance.instance_quota_release_outbox (
 tenant_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 event_id uuid NOT NULL,
 original_create_operation_id uuid NOT NULL,
 charges jsonb NOT NULL,
 acknowledged boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,instance_id),
 UNIQUE(tenant_id,event_id),
 FOREIGN KEY(tenant_id,instance_id) REFERENCES instance.instances(tenant_id,instance_id)
);
