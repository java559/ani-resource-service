-- name: GetInstance :one
SELECT snapshot,version,desired,created_at,updated_at
FROM instance.instances
WHERE tenant_id=sqlc.arg(tenant_id) AND instance_id=sqlc.arg(instance_id);

-- name: ListInstances :many
SELECT snapshot,version,desired,created_at,updated_at
FROM instance.instances
WHERE tenant_id=sqlc.arg(tenant_id) AND instance_id::text>sqlc.arg(after_id)::text
ORDER BY instance_id LIMIT sqlc.arg(page_size);
