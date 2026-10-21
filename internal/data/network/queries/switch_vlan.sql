-- name: GetActiveSwitchDevice :one
SELECT d.* FROM network_device_adoptions d WHERE cluster_id=sqlc.arg(cluster_id) AND device_name=sqlc.arg(device_name) AND NOT retired FOR UPDATE;

-- name: InsertSwitchDevice :exec
INSERT INTO network_device_adoptions(resource_id,cluster_id,device_name,inventory_fingerprint,node_inventory,virtual_switch_id,switch_binding_id)
 VALUES(sqlc.arg(resource_id),sqlc.arg(cluster_id),sqlc.arg(device_name),sqlc.arg(inventory_fingerprint),sqlc.arg(node_inventory),sqlc.arg(virtual_switch_id),sqlc.arg(switch_binding_id));

-- name: InsertSwitchVlan :exec
INSERT INTO network_vlan_networks(resource_id,cluster_id,device_id,vlan_id,virtual_switch_id)
 VALUES(sqlc.arg(resource_id),sqlc.arg(cluster_id),sqlc.arg(device_id),sqlc.arg(vlan_id),sqlc.arg(virtual_switch_id));

-- name: RegisterSwitchDevice :execrows
UPDATE network_platform_resources SET state='available',reason='',updated_at=clock_timestamp()
 WHERE resource_id=sqlc.arg(resource_id) AND kind='device' AND provider_uid='' AND pending_action='';

-- name: RetireSwitchDevices :execrows
UPDATE network_device_adoptions d SET retired=true,registration_version=registration_version+1
 WHERE d.virtual_switch_id=sqlc.arg(virtual_switch_id) AND NOT d.retired
 AND NOT EXISTS(SELECT 1 FROM network_vlan_networks v JOIN network_platform_resources p USING(resource_id)
 WHERE v.device_id=d.resource_id AND (NOT v.retired OR p.state<>'deleted' OR p.pending_action<>''))
 AND NOT EXISTS(SELECT 1 FROM network_platform_resources p WHERE p.resource_id=d.resource_id AND p.pending_action<>'');
