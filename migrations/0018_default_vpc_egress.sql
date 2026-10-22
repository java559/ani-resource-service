-- Platform aggregate: no tenant rows, no adoption of native objects.
CREATE TABLE network_default_vpc_egresses (
 resource_id text PRIMARY KEY CHECK(resource_id ~ '^dve_[0-9a-f]{32}$'),
 cluster_id text NOT NULL,
 cluster_uid text NOT NULL CHECK(cluster_uid<>''),
 default_vpc_namespace text NOT NULL CHECK(default_vpc_namespace='kcn-system'),
 default_vpc_name text NOT NULL CHECK(default_vpc_name='kcn-cluster'),
 default_vpc_uid text NOT NULL CHECK(default_vpc_uid<>''),
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 128),
 pool_id text NOT NULL,
 pool_revision bigint NOT NULL CHECK(pool_revision>0),
 pool_provider_uid text NOT NULL CHECK(pool_provider_uid<>''),
 admission_hash text NOT NULL CHECK(admission_hash ~ '^[0-9a-f]{64}$'),
 admitted_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'provisioning' CHECK(state IN('provisioning','available','degraded','failed','deleting','deleted')),
 phase text NOT NULL DEFAULT 'create_eip' CHECK(phase IN('create_eip','create_snat','ready','delete_snat','delete_eip','deleted')),
 reason text NOT NULL DEFAULT '',
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 last_operation_id uuid NOT NULL,
 eip_provider_name text NOT NULL,
 eip_provider_uid text NOT NULL DEFAULT '',
 eip_binding_id uuid NOT NULL UNIQUE,
 snat_provider_name text NOT NULL,
 snat_provider_uid text NOT NULL DEFAULT '',
 snat_binding_id uuid NOT NULL UNIQUE,
 address text NOT NULL DEFAULT '',
 applied_enabled boolean,
 target_generation bigint NOT NULL DEFAULT 0,
 pending_action text NOT NULL DEFAULT '' CHECK(pending_action IN('','create_eip','create_snat','delete_snat','delete_eip')),
 pending_unknown boolean NOT NULL DEFAULT false,
 pending_since timestamptz,
 lease_owner uuid,
 lease_until timestamptz,
 lease_epoch bigint NOT NULL DEFAULT 0,
 next_run_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 observed_at timestamptz,
 retired_at timestamptz,
 UNIQUE(cluster_id,resource_id),
 UNIQUE(cluster_id,default_vpc_namespace,eip_provider_name),
 UNIQUE(cluster_id,default_vpc_namespace,snat_provider_name),
 FOREIGN KEY(cluster_id,pool_id,pool_revision) REFERENCES network_public_pools(cluster_id,resource_id,config_revision),
 CHECK((pending_action='')=(pending_since IS NULL)),
 CHECK(NOT pending_unknown OR pending_action<>''),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL)),
 CHECK((state='deleted')=(retired_at IS NOT NULL)),
 CHECK(state<>'deleted' OR (phase='deleted' AND pending_action='' AND NOT pending_unknown))
);
CREATE UNIQUE INDEX network_default_vpc_egress_active_vpc ON network_default_vpc_egresses(cluster_id,default_vpc_namespace,default_vpc_name) WHERE retired_at IS NULL;
CREATE UNIQUE INDEX network_default_vpc_egress_active_name ON network_default_vpc_egresses(cluster_id,name) WHERE retired_at IS NULL;
CREATE INDEX network_default_vpc_egress_due ON network_default_vpc_egresses(next_run_at) WHERE retired_at IS NULL;
CREATE TABLE network_default_vpc_egress_operations (
 operation_id uuid PRIMARY KEY,
 resource_id text NOT NULL REFERENCES network_default_vpc_egresses(resource_id),
 kind text NOT NULL CHECK(kind IN('create_default_vpc_egress','delete_default_vpc_egress')),
 state text NOT NULL CHECK(state IN('queued','running','retrying','blocked','succeeded','failed')),
 reason text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 completed_at timestamptz,
 UNIQUE(resource_id,operation_id),
 CHECK((state IN('succeeded','failed'))=(completed_at IS NOT NULL)),
 CHECK(kind<>'delete_default_vpc_egress' OR state<>'failed')
);
CREATE UNIQUE INDEX network_default_vpc_egress_active_operation ON network_default_vpc_egress_operations(resource_id) WHERE completed_at IS NULL;
ALTER TABLE network_default_vpc_egresses ADD FOREIGN KEY(resource_id,last_operation_id) REFERENCES network_default_vpc_egress_operations(resource_id,operation_id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE network_default_vpc_egress_idempotency (
 cluster_id text NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
 resource_id text NOT NULL,
 operation_id uuid NOT NULL,
 response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'),
 created_at timestamptz NOT NULL,
 PRIMARY KEY(cluster_id,idempotency_key),
 FOREIGN KEY(cluster_id,resource_id) REFERENCES network_default_vpc_egresses(cluster_id,resource_id),
 FOREIGN KEY(resource_id,operation_id) REFERENCES network_default_vpc_egress_operations(resource_id,operation_id)
);
CREATE TABLE network_default_vpc_egress_history (
 history_id uuid PRIMARY KEY,
 resource_id text NOT NULL,
 operation_id uuid NOT NULL,
 event text NOT NULL,
 actor_ref text NOT NULL DEFAULT '',
 caller_ref text NOT NULL DEFAULT '',
 detail jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(detail)='object'),
 created_at timestamptz NOT NULL,
 FOREIGN KEY(resource_id,operation_id) REFERENCES network_default_vpc_egress_operations(resource_id,operation_id)
);
