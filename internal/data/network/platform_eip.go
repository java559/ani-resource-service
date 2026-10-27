package data

import (
	"context"
	"github.com/jackc/pgx/v5"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network/sqlcgen"
)

func platformEIP(row sqlcgen.NetworkEip, poolName, bindingID, bindingState, targetKind, targetID string) biz.PlatformEIP {
	v := biz.PlatformEIP{EIP: eipResource(row), PoolID: row.PoolID, PoolName: poolName, ClusterID: row.ClusterID}
	v.BindingID, v.BindingState = bindingID, bindingState
	v.BindingTarget = eipBindingTarget(targetKind, targetID, bindingState)
	return v
}
func (p *Postgres) ListPlatformEIPs(ctx context.Context, f biz.PlatformEIPFilter) ([]biz.PlatformEIP, int64, error) {
	tx, err := p.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, 0, databaseFailure(err)
	}
	defer tx.Rollback(ctx)
	q := p.queries.WithTx(tx)
	total, err := q.CountPlatformEIPs(ctx, sqlcgen.CountPlatformEIPsParams{ClusterID: p.placement.ClusterID, TenantFilter: f.TenantID, NameFilter: f.Name, AddressFilter: f.Address, PoolFilter: f.PoolID, ScopeFilter: f.Scope, StateFilter: f.State})
	if err != nil {
		return nil, 0, databaseFailure(err)
	}
	rows, err := q.ListPlatformEIPs(ctx, sqlcgen.ListPlatformEIPsParams{ClusterID: p.placement.ClusterID, TenantFilter: f.TenantID, NameFilter: f.Name, AddressFilter: f.Address, PoolFilter: f.PoolID, ScopeFilter: f.Scope, StateFilter: f.State, MaxResults: f.Limit, PageOffset: f.Offset})
	if err != nil {
		return nil, 0, databaseFailure(err)
	}
	values := make([]biz.PlatformEIP, 0, len(rows))
	for _, r := range rows {
		values = append(values, platformEIP(r.NetworkEip, r.PoolName, r.BindingID, r.BindingState, r.BindingTargetKind, r.BindingTargetID))
	}
	return values, total, nil
}
func (p *Postgres) GetPlatformEIP(ctx context.Context, id string) (biz.PlatformEIP, error) {
	r, err := p.queries.GetPlatformEIP(ctx, sqlcgen.GetPlatformEIPParams{ClusterID: p.placement.ClusterID, EipID: id})
	if err != nil {
		return biz.PlatformEIP{}, databaseFailure(err)
	}
	return platformEIP(r.NetworkEip, r.PoolName, r.BindingID, r.BindingState, r.BindingTargetKind, r.BindingTargetID), nil
}
