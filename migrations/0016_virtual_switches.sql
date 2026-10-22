-- Platform metadata must exist before a cluster is selected. These tables do
-- not use a synthetic cluster/tenant or a provider resource identity.
CREATE TABLE network_virtual_switches (
 resource_id text PRIMARY KEY CHECK (resource_id ~ '^vsw_[0-9a-f]{32}$'),
 name text NOT NULL CHECK (length(name) BETWEEN 1 AND 63 AND name ~ '^[a-zA-Z0-9]([a-zA-Z0-9_-]*[a-zA-Z0-9])?$'),
 state text NOT NULL DEFAULT 'available' CHECK (state IN ('available','degraded','deleting','deleted')),
 reason text NOT NULL DEFAULT '',
 version bigint NOT NULL DEFAULT 1 CHECK (version>0),
 created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 last_operation_id uuid NOT NULL,
 cluster_id text NOT NULL DEFAULT '',
 binding_id uuid,
 binding_version bigint NOT NULL DEFAULT 0 CHECK (binding_version>=0),
 binding_state text NOT NULL DEFAULT 'unbound' CHECK (binding_state IN ('unbound','pending','configured','degraded','cleaning','cleaned')),
 membership_fingerprint text NOT NULL DEFAULT '',
 nodes jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(nodes)='array'),
 CHECK ((binding_id IS NULL)=(cluster_id='')),
 CHECK ((binding_id IS NULL)=(binding_version=0)),
 CHECK (binding_id IS NULL OR membership_fingerprint ~ '^[0-9a-f]{64}$'),
 UNIQUE(resource_id,cluster_id), UNIQUE(binding_id)
);
CREATE UNIQUE INDEX network_virtual_switch_active_name ON network_virtual_switches(name) WHERE state<>'deleted';
CREATE INDEX network_virtual_switch_page ON network_virtual_switches(created_at DESC,resource_id DESC);

CREATE TABLE network_virtual_switch_operations (
 operation_id uuid PRIMARY KEY,
 resource_id text NOT NULL REFERENCES network_virtual_switches(resource_id),
 kind text NOT NULL CHECK (kind IN ('create_virtual_switch','delete_virtual_switch')),
 state text NOT NULL CHECK (state IN ('queued','running','retrying','blocked','succeeded')),
 reason text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 completed_at timestamptz,
 CHECK ((state='succeeded')=(completed_at IS NOT NULL)),
 UNIQUE(resource_id,operation_id)
);
ALTER TABLE network_virtual_switches ADD FOREIGN KEY(resource_id,last_operation_id)
 REFERENCES network_virtual_switch_operations(resource_id,operation_id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX network_virtual_switch_active_operation ON network_virtual_switch_operations(resource_id) WHERE completed_at IS NULL;

CREATE TABLE network_virtual_switch_idempotency (
 operation_kind text NOT NULL CHECK (operation_kind='create_virtual_switch'),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
 resource_id text NOT NULL,
 operation_id uuid NOT NULL,
 response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'),
 created_at timestamptz NOT NULL,
 PRIMARY KEY(operation_kind,idempotency_key),
 FOREIGN KEY(resource_id,operation_id) REFERENCES network_virtual_switch_operations(resource_id,operation_id)
);
CREATE TABLE network_virtual_switch_reconciliations (
 resource_id text PRIMARY KEY REFERENCES network_virtual_switches(resource_id),
 next_run_at timestamptz NOT NULL,
 lease_owner uuid,
 lease_until timestamptz,
 lease_epoch bigint NOT NULL DEFAULT 0,
 CHECK ((lease_owner IS NULL)=(lease_until IS NULL))
);
CREATE INDEX network_virtual_switch_due ON network_virtual_switch_reconciliations(next_run_at);
CREATE TABLE network_virtual_switch_history (
 history_id uuid PRIMARY KEY,
 resource_id text NOT NULL REFERENCES network_virtual_switches(resource_id),
 operation_id uuid,
 event text NOT NULL,
 actor_ref text NOT NULL DEFAULT '',
 caller_ref text NOT NULL DEFAULT '',
 detail jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL,
 FOREIGN KEY(resource_id,operation_id) REFERENCES network_virtual_switch_operations(resource_id,operation_id)
);

ALTER TABLE network_device_adoptions ADD COLUMN virtual_switch_id text;
ALTER TABLE network_device_adoptions ADD COLUMN switch_binding_id uuid;
ALTER TABLE network_device_adoptions ADD COLUMN registration_version bigint NOT NULL DEFAULT 1 CHECK(registration_version>0);
ALTER TABLE network_device_adoptions ADD COLUMN retired boolean NOT NULL DEFAULT false;
ALTER TABLE network_device_adoptions ADD FOREIGN KEY(virtual_switch_id,cluster_id)
 REFERENCES network_virtual_switches(resource_id,cluster_id);
ALTER TABLE network_device_adoptions ADD FOREIGN KEY(switch_binding_id) REFERENCES network_virtual_switches(binding_id);
ALTER TABLE network_device_adoptions ADD CHECK((virtual_switch_id IS NULL)=(switch_binding_id IS NULL));
ALTER TABLE network_device_adoptions DROP CONSTRAINT network_device_adoptions_cluster_id_device_name_key;
CREATE UNIQUE INDEX network_device_active_slot ON network_device_adoptions(cluster_id,device_name) WHERE NOT retired;
ALTER TABLE network_vlan_networks ADD COLUMN virtual_switch_id text;
ALTER TABLE network_vlan_networks ADD FOREIGN KEY(virtual_switch_id,cluster_id)
 REFERENCES network_virtual_switches(resource_id,cluster_id);
ALTER TABLE network_platform_idempotency DROP CONSTRAINT network_platform_idempotency_fingerprint_version_check;
ALTER TABLE network_platform_idempotency ADD CHECK(fingerprint_version IN (1,2));
