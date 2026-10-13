-- LB-DEFERRED-01: full accepted configuration and Provider projection advance
-- independently. Reservation-only records are not promoted into products.
ALTER TABLE network_load_balancers ADD COLUMN provider_target_version bigint;
UPDATE network_load_balancers l SET provider_target_version=l.desired_version
 WHERE EXISTS (SELECT 1 FROM network_lb_configurations c
  WHERE (c.tenant_id,c.cluster_id,c.namespace,c.lb_id,c.config_version)=
   (l.tenant_id,l.cluster_id,l.namespace,l.lb_id,l.desired_version));
ALTER TABLE network_load_balancers
 ADD CONSTRAINT network_lb_provider_versions CHECK
  (provider_target_version IS NULL OR provider_target_version>0),
 ADD CONSTRAINT network_lb_applied_provider_target CHECK
  (0<=applied_version AND applied_version<=COALESCE(provider_target_version,0)
   AND COALESCE(provider_target_version,0)<=desired_version),
 ADD CONSTRAINT network_lb_provider_target_configuration FOREIGN KEY
  (tenant_id,cluster_id,namespace,lb_id,provider_target_version)
  REFERENCES network_lb_configurations(tenant_id,cluster_id,namespace,lb_id,config_version)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE network_load_balancers DROP CONSTRAINT network_load_balancers_configuration_state_check;
ALTER TABLE network_load_balancers ADD CONSTRAINT network_load_balancers_configuration_state_check
 CHECK (configuration_state IN ('pending','applying','configured','degraded','unknown','waiting_for_backends'));

-- Only fenced Finish writes completed retirement facts. Binding reincarnation
-- reads this immutable record; it is not another work queue.
ALTER TABLE network_operations ADD UNIQUE (tenant_id,lb_id,operation_id,state);
CREATE TABLE network_lb_retired_bindings (
 tenant_id uuid NOT NULL, cluster_id text NOT NULL, namespace text NOT NULL,
 lb_id text NOT NULL, binding_id uuid NOT NULL, provider_name text NOT NULL CHECK (provider_name<>''),
 provider_uid text NOT NULL CHECK (provider_uid<>''), cleanup_target_version bigint NOT NULL CHECK (cleanup_target_version>0),
 operation_id uuid NOT NULL, operation_state text NOT NULL DEFAULT 'succeeded' CHECK (operation_state='succeeded'),
 vip_occupied_revision text NOT NULL DEFAULT '', vip_absence_revision text NOT NULL DEFAULT '',
 cleanup_completed_at timestamptz NOT NULL,
 PRIMARY KEY (tenant_id,lb_id,binding_id),
 UNIQUE (tenant_id,cluster_id,namespace,lb_id,binding_id),
 FOREIGN KEY (tenant_id,cluster_id,namespace,lb_id)
  REFERENCES network_load_balancers(tenant_id,cluster_id,namespace,lb_id),
 FOREIGN KEY (tenant_id,cluster_id,namespace,lb_id,cleanup_target_version)
  REFERENCES network_lb_configurations(tenant_id,cluster_id,namespace,lb_id,config_version),
 FOREIGN KEY (tenant_id,lb_id,operation_id,operation_state)
  REFERENCES network_operations(tenant_id,lb_id,operation_id,state)
  DEFERRABLE INITIALLY DEFERRED
);
CREATE FUNCTION network_lb_retirement_is_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'completed LB retirement facts are immutable'; END $$;
CREATE TRIGGER network_lb_retirement_immutable BEFORE UPDATE OR DELETE
 ON network_lb_retired_bindings FOR EACH ROW EXECUTE FUNCTION network_lb_retirement_is_immutable();
