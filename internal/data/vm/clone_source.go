package vm

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

// CloneReleaseKinds is the closed audit set accepted by the reference table.
var CloneReleaseKinds = map[string]bool{
	"REGISTERED": true, "FAILED_NO_OUTPUT": true, "GONE_NO_OUTPUT": true,
	"ADMIN_RELEASED": true, "CANCELLED": true,
}

// ReleaseCloneSourceHELD writes the release audit columns. A replayed release is
// idempotent; a HELD row is never partially written because the table check
// constraint requires every audit column.
func (p *Postgres) ReleaseCloneSourceHELD(ctx context.Context, r biz.CloneSourceRelease) error {
	if !biz.UUID(r.TenantID) || !biz.UUID(r.TargetVMID) || !CloneReleaseKinds[r.Kind] || r.By == "" || r.Reason == "" || r.Evidence == "" {
		return biz.ErrInvalid
	}
	tag, e := p.pool.Exec(ctx, `UPDATE vm.clone_source_references SET state='RELEASED',release_kind=$3,release_reason=$4,released_by=$5,release_evidence=$6,released_at=now() WHERE tenant_id=$1 AND target_vm_id=$2 AND state='HELD'`, r.TenantID, r.TargetVMID, r.Kind, r.Reason, r.By, r.Evidence)
	if e != nil {
		return e
	}
	if tag.RowsAffected() == 1 {
		return nil
	}
	var state string
	if e = p.pool.QueryRow(ctx, "SELECT state FROM vm.clone_source_references WHERE tenant_id=$1 AND target_vm_id=$2", r.TenantID, r.TargetVMID).Scan(&state); e != nil {
		if errors.Is(e, pgx.ErrNoRows) {
			return biz.ErrNotFound
		}
		return e
	}
	if state == "RELEASED" {
		return nil
	}
	return biz.ErrVersion
}

// CloneSourceReference returns the audit row of one target.
func (p *Postgres) CloneSourceReference(ctx context.Context, tenant, target string) (biz.CloneSourceReference, error) {
	var r biz.CloneSourceReference
	var releasedAt *time.Time
	e := p.pool.QueryRow(ctx, `SELECT tenant_id,source_vm_id,target_vm_id,create_operation_id,source_native_uid,source_version,state,release_kind,release_reason,released_by,release_evidence,released_at FROM vm.clone_source_references WHERE tenant_id=$1 AND target_vm_id=$2`, tenant, target).Scan(&r.TenantID, &r.SourceVMID, &r.TargetVMID, &r.CreateOperationID, &r.SourceNativeUID, &r.SourceVersion, &r.State, &r.ReleaseKind, &r.ReleaseReason, &r.ReleasedBy, &r.ReleaseEvidence, &releasedAt)
	if e != nil {
		return r, notFound(e)
	}
	r.ReleasedAt = releasedAt
	return r, nil
}

var _ biz.CloneSourcePort = (*Postgres)(nil)
