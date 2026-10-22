-- Include retired historical registrations: each interface ever used by this
-- binding needs its own physical cleanup proof before the group name is freed.
-- name: ListVirtualSwitchDevices :many
SELECT * FROM network_device_adoptions WHERE cluster_id=sqlc.arg(cluster_id) AND coalesce(virtual_switch_id,'')=sqlc.arg(switch_id)::text ORDER BY resource_id;
