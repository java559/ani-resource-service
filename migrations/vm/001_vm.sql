CREATE SCHEMA vm;
CREATE TABLE vm.schema_version (version integer PRIMARY KEY, checksum text NOT NULL);
CREATE TABLE vm.virtual_machines (
 tenant_id uuid NOT NULL, id uuid NOT NULL, create_operation_id uuid NOT NULL,
 request_digest text NOT NULL, spec_digest text NOT NULL, initial_spec jsonb NOT NULL,
 current_configuration jsonb NOT NULL, version bigint NOT NULL CHECK(version>0),
 closing boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id),
 UNIQUE(tenant_id,create_operation_id)
);
CREATE TABLE vm.disks (
 tenant_id uuid NOT NULL, vm_id uuid NOT NULL, disk_id uuid NOT NULL,
 volume_name text NOT NULL, serial text NOT NULL, record jsonb NOT NULL,
 PRIMARY KEY(tenant_id,vm_id,disk_id), UNIQUE(tenant_id,vm_id,volume_name),
 FOREIGN KEY(tenant_id,vm_id) REFERENCES vm.virtual_machines(tenant_id,id)
);
CREATE TABLE vm.operations (
 tenant_id uuid NOT NULL, id uuid NOT NULL, vm_id uuid NOT NULL,
 action text NOT NULL, idempotency_key text NOT NULL, request_digest text NOT NULL,
 intent jsonb NOT NULL, intent_version bigint NOT NULL CHECK(intent_version>0),
 state text NOT NULL CHECK(state IN ('accepted','running','succeeded','failed','unknown')),
 record jsonb NOT NULL, lease_token uuid, lease_until timestamptz,
 next_attempt_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id),
 UNIQUE(tenant_id,vm_id,action,idempotency_key),
 FOREIGN KEY(tenant_id,vm_id) REFERENCES vm.virtual_machines(tenant_id,id)
);
CREATE INDEX vm_work ON vm.operations(next_attempt_at) WHERE state IN ('accepted','running','unknown');
CREATE TABLE vm.snapshots (
 tenant_id uuid NOT NULL, vm_id uuid NOT NULL, snapshot_id uuid NOT NULL,
 record jsonb NOT NULL, PRIMARY KEY(tenant_id,vm_id,snapshot_id),
 FOREIGN KEY(tenant_id,vm_id) REFERENCES vm.virtual_machines(tenant_id,id)
);
