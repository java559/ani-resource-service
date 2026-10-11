ALTER TABLE network_lb_configuration_listeners
 DROP CONSTRAINT network_lb_configuration_listeners_protocol_check,
 ADD CHECK (protocol IN ('HTTP','HTTPS','TCP','UDP','SSL')),
 ADD COLUMN parameters jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(parameters)='object'),
 ADD COLUMN health_protocol text NOT NULL DEFAULT 'TCP' CHECK (health_protocol IN ('TCP','HTTP')),
 ADD COLUMN health_path text NOT NULL DEFAULT '',
 ADD COLUMN health_hostname text NOT NULL DEFAULT '';

-- Replace only kind-dependent component guards; keep all identity, mutation,
-- version, tenant and namespace foreign keys unchanged.
DO $$ DECLARE guard text; BEGIN
 FOR guard IN SELECT conname FROM pg_constraint
  WHERE conrelid='network_lb_components'::regclass AND contype='c'
   AND pg_get_constraintdef(oid) LIKE '%kind%'
 LOOP EXECUTE format('ALTER TABLE network_lb_components DROP CONSTRAINT %I',guard); END LOOP;
END $$;
ALTER TABLE network_lb_components
 ADD CHECK (kind IN ('route','tcp_route','udp_route','policy','client_policy','patch_policy','backend')),
 ADD CHECK ((kind='backend')=(member_id IS NOT NULL)),
 ADD CHECK ((kind IN ('route','tcp_route','udp_route','policy','client_policy','patch_policy'))=(listener_id IS NOT NULL));

-- Envoy health timeout and interval are independent positive durations.
DO $$ DECLARE relation text; guard text; BEGIN
 FOREACH relation IN ARRAY ARRAY['network_lb_configurations','network_lb_configuration_listeners'] LOOP
  FOR guard IN SELECT conname FROM pg_constraint
   WHERE conrelid=relation::regclass AND contype='c'
    AND pg_get_constraintdef(oid) LIKE '%timeout_seconds%'
  LOOP EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',relation,guard); END LOOP;
  EXECUTE format('ALTER TABLE %I ADD CHECK (timeout_seconds BETWEEN 1 AND 4294967295)',relation);
 END LOOP;
END $$;
