-- name: LockLegacyDeviceAdoption :one
SELECT * FROM network_device_adoptions WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id) FOR UPDATE;

-- name: LegacyDeviceDependencies :one
SELECT count(*)::bigint FROM (SELECT 1 FROM network_vlan_networks v JOIN network_platform_resources r ON r.resource_id=v.resource_id
 WHERE v.cluster_id=sqlc.arg(cluster_id) AND v.device_id=sqlc.arg(resource_id)
 AND (NOT v.retired OR r.state<>'deleted' OR r.pending_action<>'')
 UNION ALL SELECT 1 FROM network_public_pools p JOIN network_vlan_networks v ON p.vlan_network_id=v.resource_id
 JOIN network_platform_resources r ON r.resource_id=p.resource_id
 WHERE v.cluster_id=sqlc.arg(cluster_id) AND v.device_id=sqlc.arg(resource_id)
 AND (r.state<>'deleted' OR r.pending_action<>'')) AS active_references;

-- name: GetLegacyDeviceRetirementReplay :one
SELECT * FROM network_legacy_device_retirement_idempotency WHERE cluster_id=sqlc.arg(cluster_id) AND idempotency_key=sqlc.arg(idempotency_key);

-- name: GetLegacyDeviceRetirement :one
SELECT * FROM network_legacy_device_retirements WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id);

-- name: LockLegacyDeviceRetirement :one
SELECT * FROM network_legacy_device_retirements WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id) FOR UPDATE;

-- name: LockLegacyDeviceExecutor :one
SELECT * FROM network_platform_reconciliations WHERE resource_id=sqlc.arg(resource_id) FOR UPDATE;

-- name: CloseLegacyDeviceRegistration :execrows
UPDATE network_device_adoptions SET registration_closed=true,registration_version=registration_version+1
 WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id) AND virtual_switch_id IS NULL AND NOT retired AND NOT registration_closed;

-- name: FenceLegacyDeviceResource :one
UPDATE network_platform_resources SET version=version+1,state='deleting',reason='DEVICE_REGISTRATION_CLOSING',updated_at=clock_timestamp()
 WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id) AND kind='device' AND version=sqlc.arg(version) RETURNING *;

-- name: FenceLegacyDeviceExecutor :one
UPDATE network_platform_reconciliations SET lease_epoch=lease_epoch+1,lease_owner=NULL,lease_until=NULL,next_run_at=clock_timestamp()+interval '100 years'
 WHERE resource_id=sqlc.arg(resource_id) RETURNING *;

-- name: CloseLegacyDeviceAdoptionOperation :exec
UPDATE network_platform_operations SET state='failed',reason='DEVICE_REGISTRATION_CLOSING',completed_at=clock_timestamp(),updated_at=clock_timestamp(),next_attempt_at=NULL
 WHERE resource_id=sqlc.arg(resource_id) AND kind='adopt_device' AND completed_at IS NULL;

-- name: InsertLegacyDeviceRetirement :one
INSERT INTO network_legacy_device_retirements(resource_id,cluster_id,operation_id,expected_version,fence_version,binding_id,device_name,node_inventory,pre_managed,config_uid,pending_unknown,legacy_pending_action,executor_epoch,drain_after,next_run_at,created_at,updated_at)
 VALUES(sqlc.arg(resource_id),sqlc.arg(cluster_id),sqlc.arg(operation_id),sqlc.arg(expected_version),sqlc.arg(fence_version),sqlc.arg(binding_id),sqlc.arg(device_name),sqlc.arg(node_inventory),sqlc.arg(pre_managed),sqlc.arg(config_uid),sqlc.arg(pending_unknown),sqlc.arg(legacy_pending_action),sqlc.arg(executor_epoch),sqlc.arg(drain_after),sqlc.arg(drain_after),sqlc.arg(created_at),sqlc.arg(created_at)) RETURNING *;

-- name: InsertLegacyDeviceRetirementReplay :exec
INSERT INTO network_legacy_device_retirement_idempotency(cluster_id,idempotency_key,fingerprint,resource_id,operation_id,response,created_at)
 VALUES(sqlc.arg(cluster_id),sqlc.arg(idempotency_key),sqlc.arg(fingerprint),sqlc.arg(resource_id),sqlc.arg(operation_id),sqlc.arg(response),sqlc.arg(created_at));

-- name: InsertLegacyDeviceRetirementHistory :exec
INSERT INTO network_legacy_device_retirement_history(history_id,resource_id,operation_id,event,actor_ref,caller_ref,detail,created_at)
 VALUES(sqlc.arg(history_id),sqlc.arg(resource_id),sqlc.arg(operation_id),sqlc.arg(event),sqlc.arg(actor_ref),sqlc.arg(caller_ref),sqlc.arg(detail),clock_timestamp());

-- name: DueLegacyDeviceRetirement :one
SELECT * FROM network_legacy_device_retirements WHERE cluster_id=sqlc.arg(cluster_id) AND state<>'retired'
 AND next_run_at<=clock_timestamp() AND drain_after<=clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp())
 ORDER BY next_run_at,resource_id LIMIT 1 FOR UPDATE SKIP LOCKED;

-- name: LeaseLegacyDeviceRetirement :one
UPDATE network_legacy_device_retirements SET lease_owner=sqlc.arg(owner)::uuid,lease_until=clock_timestamp()+sqlc.arg(lease_micros)::bigint*interval '1 microsecond',lease_epoch=lease_epoch+1
 WHERE resource_id=sqlc.arg(resource_id) AND (lease_until IS NULL OR lease_until<=clock_timestamp()) RETURNING *;

-- name: CheckLegacyDeviceRetirementLease :one
SELECT * FROM network_legacy_device_retirements WHERE resource_id=sqlc.arg(resource_id) AND lease_owner=sqlc.arg(owner)::uuid AND lease_epoch=sqlc.arg(epoch) AND lease_until>clock_timestamp() FOR UPDATE;

-- name: PrepareLegacyDeviceRetirement :one
UPDATE network_legacy_device_retirements SET prepared=true,config_rv=sqlc.arg(config_rv),config_managed_raw=sqlc.arg(config_managed_raw),desired_managed_raw=sqlc.arg(desired_managed_raw),marker_present=sqlc.arg(marker_present),marker_value=sqlc.arg(marker_value),pending_unknown=true,version=version+1,updated_at=clock_timestamp()
 WHERE resource_id=sqlc.arg(resource_id) AND version=sqlc.arg(version) RETURNING *;

-- name: FinishLegacyDeviceRetirementAttempt :execrows
UPDATE network_legacy_device_retirements SET state=sqlc.arg(state),reason=sqlc.arg(reason),pending_unknown=CASE WHEN sqlc.arg(cleared)::bool THEN false ELSE pending_unknown END,
 completed_at=CASE WHEN sqlc.arg(cleared)::bool THEN clock_timestamp() ELSE NULL END,
 version=version+1,updated_at=clock_timestamp(),lease_owner=NULL,lease_until=NULL,next_run_at=clock_timestamp()+sqlc.arg(delay_micros)::bigint*interval '1 microsecond'
 WHERE resource_id=sqlc.arg(resource_id) AND version=sqlc.arg(version) AND lease_owner=sqlc.arg(owner)::uuid AND lease_epoch=sqlc.arg(epoch) AND lease_until>clock_timestamp();

-- name: RetireClosedLegacyDevice :execrows
UPDATE network_device_adoptions SET retired=true,registration_version=registration_version+1
 WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id) AND registration_closed AND NOT retired AND virtual_switch_id IS NULL;

-- name: CompleteLegacyDeviceResourceRetirement :execrows
UPDATE network_platform_resources SET state='deleted',reason='',pending_action='',pending_since=NULL,version=version+1,updated_at=clock_timestamp()
 WHERE cluster_id=sqlc.arg(cluster_id) AND resource_id=sqlc.arg(resource_id) AND kind='device' AND version=sqlc.arg(fence_version) AND binding_id=sqlc.arg(binding_id);
