-- Instance owns its submission and immutable binding handoff. Network tables
-- are intentionally outside this relation and transaction boundary.
CREATE TABLE instance.network_submissions (
 tenant_id uuid NOT NULL,
 instance_id uuid NOT NULL,
 submission_id uuid NOT NULL CHECK(submission_id<>'00000000-0000-0000-0000-000000000000'),
 request_key text NOT NULL CHECK(length(request_key)>0),
 generation bigint NOT NULL CHECK(generation>0),
 snapshot_revision bigint NOT NULL CHECK(snapshot_revision>0),
 state text NOT NULL CHECK(state IN ('open','closing','closed')),
 attachment_id text NOT NULL DEFAULT '',
 finalization_id uuid,
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,instance_id),
 UNIQUE(tenant_id,submission_id),
 FOREIGN KEY(tenant_id,instance_id) REFERENCES instance.instances(tenant_id,instance_id),
 CHECK((state='open')=(finalization_id IS NULL))
);
