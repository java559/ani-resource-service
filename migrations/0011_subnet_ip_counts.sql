ALTER TABLE network_subnets
 ADD COLUMN available_ip_count bigint CHECK (available_ip_count >= 0),
 ADD COLUMN used_ip_count bigint CHECK (used_ip_count >= 0);
