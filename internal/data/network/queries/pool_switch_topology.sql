-- name: PublicPoolVirtualSwitchTopology :one
SELECT s.resource_id AS switch_id,s.binding_id,s.binding_version,s.name AS group_name,
 s.membership_fingerprint,d.inventory_fingerprint AS device_fingerprint
FROM network_public_pools p JOIN network_vlan_networks v ON v.cluster_id=p.cluster_id AND v.resource_id=p.vlan_network_id
JOIN network_device_adoptions d ON d.cluster_id=v.cluster_id AND d.resource_id=v.device_id
JOIN network_virtual_switches s ON s.resource_id=v.virtual_switch_id AND s.cluster_id=v.cluster_id
WHERE p.cluster_id=sqlc.arg(cluster_id) AND p.resource_id=sqlc.arg(resource_id);
