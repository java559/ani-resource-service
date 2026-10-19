-- Explicit owner migration. Existing v1 request bytes and snapshots stay intact.
ALTER TABLE instance.instances ADD COLUMN instance_contract_version integer NOT NULL DEFAULT 1
 CHECK(instance_contract_version IN (1,2));
CREATE TABLE instance.borrowed_volume_references (
 tenant_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 mount_path text NOT NULL,
 cluster_id uuid NOT NULL,
 namespace text NOT NULL,
 pvc_name text NOT NULL,
 pvc_uid text NOT NULL CHECK(pvc_uid<>''),
 read_only boolean NOT NULL,
 ownership text NOT NULL DEFAULT 'borrowed' CHECK(ownership='borrowed'),
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','released')),
 PRIMARY KEY(tenant_id,instance_id,mount_path),
 FOREIGN KEY(tenant_id,instance_id) REFERENCES instance.instances(tenant_id,instance_id)
);
CREATE INDEX borrowed_volume_references_active ON instance.borrowed_volume_references(cluster_id,namespace,pvc_name,pvc_uid)
 WHERE state='active';
