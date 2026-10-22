package data

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network/sqlcgen"
)

// KC HTTP requests are bounded to 10 seconds. A pre-closure executor has a
// persisted lease deadline; cleanup begins only after that deadline plus the
// request bound and cancellation margin. This is a durable phase, not a sleep.
const legacyDeviceRequestDrain = 15 * time.Second

func retirementSnapshot(r sqlcgen.NetworkLegacyDeviceRetirement) biz.DeviceRetirement {
	return biz.DeviceRetirement{DeviceID: r.ResourceID, OperationID: r.OperationID, State: r.State, Reason: r.Reason, Version: r.Version, ExpectedVersion: r.ExpectedVersion, ConfigUID: r.ConfigUid, FenceVersion: r.FenceVersion, PendingUnknown: r.PendingUnknown, CreatedAt: r.CreatedAt, UpdatedAt: r.UpdatedAt, CompletedAt: r.CompletedAt}
}
func retirementWork(r sqlcgen.NetworkLegacyDeviceRetirement, now time.Time) (biz.LegacyDeviceRetirementWork, error) {
	w := biz.LegacyDeviceRetirementWork{Retirement: retirementSnapshot(r), ClusterID: r.ClusterID, BindingID: r.BindingID, DeviceName: r.DeviceName, Owner: textValue(r.LeaseOwner), PreManaged: r.PreManaged, Prepared: r.Prepared, Epoch: r.LeaseEpoch, ExecutorEpoch: r.ExecutorEpoch, Now: now, DrainAfter: r.DrainAfter, Plan: biz.LegacyDeviceRetirementPlan{ConfigUID: r.ConfigUid, ConfigRV: r.ConfigRv, ManagedRaw: r.ConfigManagedRaw, DesiredManagedRaw: r.DesiredManagedRaw, MarkerPresent: r.MarkerPresent, MarkerValue: r.MarkerValue}}
	err := json.Unmarshal(r.NodeInventory, &w.Nodes)
	return w, databaseFailure(err)
}
func legacyRetirementReplay(ctx context.Context, q *sqlcgen.Queries, cluster string, i biz.DeviceRetirementIntent) (biz.DeviceRetirement, bool, error) {
	r, err := q.GetLegacyDeviceRetirementReplay(ctx, sqlcgen.GetLegacyDeviceRetirementReplayParams{ClusterID: cluster, IdempotencyKey: i.IdempotencyKey})
	if errors.Is(err, pgx.ErrNoRows) {
		return biz.DeviceRetirement{}, false, nil
	}
	if err != nil {
		return biz.DeviceRetirement{}, false, databaseFailure(err)
	}
	if r.Fingerprint != contentHash(struct {
		DeviceID        string
		ExpectedVersion int64
	}{i.DeviceID, i.ExpectedVersion}) {
		return biz.DeviceRetirement{}, false, biz.Fail(biz.IdempotencyConflict, "retirement key already accepted a different intent")
	}
	var v biz.DeviceRetirement
	if err = json.Unmarshal(r.Response, &v); err != nil {
		return v, false, databaseFailure(err)
	}
	if v.DeviceID != r.ResourceID || v.OperationID != r.OperationID {
		return v, false, biz.Fail(biz.DependencyUnavailable, "invalid retirement replay snapshot")
	}
	return v, true, nil
}
func (p *Postgres) RetireNetworkDevice(ctx context.Context, i biz.DeviceRetirementIntent, a biz.Attribution) (biz.DeviceRetirement, error) {
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return biz.DeviceRetirement{}, databaseFailure(err)
	}
	defer tx.Rollback(ctx)
	q := p.queries.WithTx(tx)
	if err = q.LockPlatformKey(ctx, sqlcgen.LockPlatformKeyParams{ClusterID: p.placement.ClusterID, OperationKind: "retire_legacy_device", IdempotencyKey: i.IdempotencyKey}); err != nil {
		return biz.DeviceRetirement{}, databaseFailure(err)
	}
	if v, found, err := legacyRetirementReplay(ctx, q, p.placement.ClusterID, i); found || err != nil {
		return v, err
	}
	if err = q.LockPlatformCluster(ctx, sqlcgen.LockPlatformClusterParams{ClusterID: p.placement.ClusterID}); err != nil {
		return biz.DeviceRetirement{}, databaseFailure(err)
	}
	r, err := q.LockPlatform(ctx, sqlcgen.LockPlatformParams{ClusterID: p.placement.ClusterID, Kind: "device", ResourceID: i.DeviceID})
	if err != nil {
		return biz.DeviceRetirement{}, databaseFailure(err)
	}
	d, err := q.LockLegacyDeviceAdoption(ctx, sqlcgen.LockLegacyDeviceAdoptionParams{ClusterID: p.placement.ClusterID, ResourceID: i.DeviceID})
	if err != nil {
		return biz.DeviceRetirement{}, databaseFailure(err)
	}
	if d.VirtualSwitchID != nil {
		return biz.DeviceRetirement{}, biz.Fail(biz.InvalidArgument, "switch-owned registrations retire through their switch lifecycle")
	}
	now, err := q.DatabaseTime(ctx)
	if err != nil {
		return biz.DeviceRetirement{}, databaseFailure(err)
	}
	job, err := q.GetLegacyDeviceRetirement(ctx, sqlcgen.GetLegacyDeviceRetirementParams{ClusterID: p.placement.ClusterID, ResourceID: i.DeviceID})
	if err == nil {
		if job.ExpectedVersion != i.ExpectedVersion {
			return biz.DeviceRetirement{}, biz.Fail(biz.ResourceBusy, "retirement expected version differs from the accepted closure")
		}
	} else {
		if !errors.Is(err, pgx.ErrNoRows) {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		if r.Version != i.ExpectedVersion || d.RegistrationClosed || d.Retired || r.State == "deleted" {
			return biz.DeviceRetirement{}, biz.Fail(biz.ResourceBusy, "device registration version changed")
		}
		blocked, err := q.LegacyDeviceDependencies(ctx, sqlcgen.LegacyDeviceDependenciesParams{ClusterID: p.placement.ClusterID, ResourceID: i.DeviceID})
		if err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		if blocked > 0 {
			return biz.DeviceRetirement{}, biz.Fail(biz.ResourceInUse, "device still has active or unresolved product references")
		}
		executor, err := q.LockLegacyDeviceExecutor(ctx, sqlcgen.LockLegacyDeviceExecutorParams{ResourceID: i.DeviceID})
		if err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		var accepted []biz.NodeInterface
		if json.Unmarshal(d.NodeInventory, &accepted) != nil || len(accepted) == 0 {
			return biz.DeviceRetirement{}, biz.Fail(biz.DependencyUnavailable, "legacy accepted inventory is missing")
		}
		preManaged, configUID := accepted[0].KCConfigured, r.ProviderUid
		for _, n := range accepted {
			if n.KCConfigured != preManaged || n.NodeUID == "" || n.NodeName == "" || n.Name != d.DeviceName || n.MAC == "" {
				return biz.DeviceRetirement{}, biz.Fail(biz.DependencyUnavailable, "legacy accepted identity is inconsistent")
			}
			if configUID == "" {
				configUID = n.KCConfigUID
			}
			if n.KCConfigUID != "" && n.KCConfigUID != configUID {
				return biz.DeviceRetirement{}, biz.Fail(biz.DependencyUnavailable, "legacy ConfigMap identities disagree")
			}
		}
		drain := now
		if executor.LeaseUntil != nil && executor.LeaseUntil.After(drain) {
			drain = *executor.LeaseUntil
		}
		drain = drain.Add(legacyDeviceRequestDrain)
		if err = affected(q.CloseLegacyDeviceRegistration(ctx, sqlcgen.CloseLegacyDeviceRegistrationParams{ClusterID: p.placement.ClusterID, ResourceID: i.DeviceID})); err != nil {
			return biz.DeviceRetirement{}, err
		}
		fenced, err := q.FenceLegacyDeviceResource(ctx, sqlcgen.FenceLegacyDeviceResourceParams{ClusterID: p.placement.ClusterID, ResourceID: i.DeviceID, Version: i.ExpectedVersion})
		if err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		fence, err := q.FenceLegacyDeviceExecutor(ctx, sqlcgen.FenceLegacyDeviceExecutorParams{ResourceID: i.DeviceID})
		if err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		if err = q.CloseLegacyDeviceAdoptionOperation(ctx, sqlcgen.CloseLegacyDeviceAdoptionOperationParams{ResourceID: i.DeviceID}); err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		job, err = q.InsertLegacyDeviceRetirement(ctx, sqlcgen.InsertLegacyDeviceRetirementParams{ResourceID: i.DeviceID, ClusterID: p.placement.ClusterID, OperationID: uuid.NewString(), ExpectedVersion: i.ExpectedVersion, FenceVersion: fenced.Version, BindingID: r.BindingID, DeviceName: d.DeviceName, NodeInventory: d.NodeInventory, PreManaged: preManaged, ConfigUid: configUID, PendingUnknown: r.PendingAction != "", LegacyPendingAction: r.PendingAction, ExecutorEpoch: fence.LeaseEpoch, DrainAfter: drain, CreatedAt: now})
		if err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
		if err = q.InsertLegacyDeviceRetirementHistory(ctx, sqlcgen.InsertLegacyDeviceRetirementHistoryParams{HistoryID: uuid.NewString(), ResourceID: i.DeviceID, OperationID: job.OperationID, Event: "registration_closed", ActorRef: a.Actor, CallerRef: a.DirectCaller, Detail: []byte(`{}`)}); err != nil {
			return biz.DeviceRetirement{}, databaseFailure(err)
		}
	}
	v := retirementSnapshot(job)
	body, err := json.Marshal(v)
	if err != nil {
		return v, err
	}
	if err = q.InsertLegacyDeviceRetirementReplay(ctx, sqlcgen.InsertLegacyDeviceRetirementReplayParams{ClusterID: p.placement.ClusterID, IdempotencyKey: i.IdempotencyKey, Fingerprint: contentHash(struct {
		DeviceID        string
		ExpectedVersion int64
	}{i.DeviceID, i.ExpectedVersion}), ResourceID: v.DeviceID, OperationID: v.OperationID, Response: body, CreatedAt: now}); err != nil {
		return v, databaseFailure(err)
	}
	return v, databaseFailure(tx.Commit(ctx))
}
func (p *Postgres) GetNetworkDeviceRetirement(ctx context.Context, id string) (biz.DeviceRetirement, error) {
	r, err := p.queries.GetLegacyDeviceRetirement(ctx, sqlcgen.GetLegacyDeviceRetirementParams{ClusterID: p.placement.ClusterID, ResourceID: id})
	return retirementSnapshot(r), databaseFailure(err)
}

func (p *Postgres) ClaimLegacyDeviceRetirement(ctx context.Context, owner string, duration time.Duration) (biz.LegacyDeviceRetirementWork, bool, error) {
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return biz.LegacyDeviceRetirementWork{}, false, databaseFailure(err)
	}
	defer tx.Rollback(ctx)
	q := p.queries.WithTx(tx)
	r, err := q.DueLegacyDeviceRetirement(ctx, sqlcgen.DueLegacyDeviceRetirementParams{ClusterID: p.placement.ClusterID})
	if errors.Is(err, pgx.ErrNoRows) {
		return biz.LegacyDeviceRetirementWork{}, false, nil
	}
	if err != nil {
		return biz.LegacyDeviceRetirementWork{}, false, databaseFailure(err)
	}
	r, err = q.LeaseLegacyDeviceRetirement(ctx, sqlcgen.LeaseLegacyDeviceRetirementParams{ResourceID: r.ResourceID, Owner: owner, LeaseMicros: duration.Microseconds()})
	if err != nil {
		return biz.LegacyDeviceRetirementWork{}, false, databaseFailure(err)
	}
	now, err := q.DatabaseTime(ctx)
	if err != nil {
		return biz.LegacyDeviceRetirementWork{}, false, databaseFailure(err)
	}
	w, err := retirementWork(r, now)
	if err != nil {
		return w, false, err
	}
	return w, true, databaseFailure(tx.Commit(ctx))
}
func (p *Postgres) lockLegacyRetirement(ctx context.Context, q *sqlcgen.Queries, w biz.LegacyDeviceRetirementWork) (sqlcgen.NetworkLegacyDeviceRetirement, error) {
	r, err := q.CheckLegacyDeviceRetirementLease(ctx, sqlcgen.CheckLegacyDeviceRetirementLeaseParams{ResourceID: w.Retirement.DeviceID, Owner: w.Owner, Epoch: w.Epoch})
	if errors.Is(err, pgx.ErrNoRows) {
		return r, biz.ErrLeaseLost
	}
	if err != nil {
		return r, databaseFailure(err)
	}
	if r.Version != w.Retirement.Version || r.OperationID != w.Retirement.OperationID || r.ClusterID != p.placement.ClusterID || r.BindingID != w.BindingID || r.State == "retired" {
		return r, biz.ErrLeaseLost
	}
	resource, err := q.LockPlatform(ctx, sqlcgen.LockPlatformParams{ClusterID: r.ClusterID, Kind: "device", ResourceID: r.ResourceID})
	if err != nil {
		return r, databaseFailure(err)
	}
	d, err := q.LockLegacyDeviceAdoption(ctx, sqlcgen.LockLegacyDeviceAdoptionParams{ClusterID: r.ClusterID, ResourceID: r.ResourceID})
	if err != nil {
		return r, databaseFailure(err)
	}
	executor, err := q.LockLegacyDeviceExecutor(ctx, sqlcgen.LockLegacyDeviceExecutorParams{ResourceID: r.ResourceID})
	if err != nil {
		return r, databaseFailure(err)
	}
	now, err := q.DatabaseTime(ctx)
	if err != nil {
		return r, databaseFailure(err)
	}
	if resource.Version != r.FenceVersion || resource.BindingID != r.BindingID || resource.State != "deleting" || !d.RegistrationClosed || d.Retired || d.VirtualSwitchID != nil || executor.LeaseEpoch != r.ExecutorEpoch || executor.LeaseOwner != nil || executor.LeaseUntil != nil || now.Before(r.DrainAfter) {
		return r, biz.ErrLeaseLost
	}
	return r, nil
}
func (p *Postgres) PrepareLegacyDeviceRetirement(ctx context.Context, w biz.LegacyDeviceRetirementWork, o biz.LegacyDeviceRetirementObservation) (biz.LegacyDeviceRetirementWork, error) {
	if o.BlockedReason != "" || o.Cleared || o.Plan.ConfigUID == "" || o.Plan.ConfigUID != w.Retirement.ConfigUID || o.Plan.ConfigRV == "" || o.CollectedAt.IsZero() || o.CollectedAt.Before(w.Now) || len(o.Hash) != 64 {
		return w, biz.Fail(biz.DependencyUnavailable, "fresh exact retirement plan required")
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return w, databaseFailure(err)
	}
	defer tx.Rollback(ctx)
	q := p.queries.WithTx(tx)
	if _, err = p.lockLegacyRetirement(ctx, q, w); err != nil {
		return w, err
	}
	blocked, err := q.LegacyDeviceDependencies(ctx, sqlcgen.LegacyDeviceDependenciesParams{ClusterID: w.ClusterID, ResourceID: w.Retirement.DeviceID})
	if err != nil {
		return w, databaseFailure(err)
	}
	if blocked > 0 {
		return w, biz.Fail(biz.ResourceInUse, "product references prevent retirement")
	}
	r, err := q.PrepareLegacyDeviceRetirement(ctx, sqlcgen.PrepareLegacyDeviceRetirementParams{ResourceID: w.Retirement.DeviceID, Version: w.Retirement.Version, ConfigRv: o.Plan.ConfigRV, ConfigManagedRaw: o.Plan.ManagedRaw, DesiredManagedRaw: o.Plan.DesiredManagedRaw, MarkerPresent: o.Plan.MarkerPresent, MarkerValue: o.Plan.MarkerValue})
	if err != nil {
		return w, databaseFailure(err)
	}
	updated, err := retirementWork(r, w.Now)
	if err != nil {
		return w, err
	}
	if err = q.InsertLegacyDeviceRetirementHistory(ctx, sqlcgen.InsertLegacyDeviceRetirementHistoryParams{HistoryID: uuid.NewString(), ResourceID: r.ResourceID, OperationID: r.OperationID, Event: "config_cleanup_prepared", Detail: []byte(`{}`)}); err != nil {
		return w, databaseFailure(err)
	}
	return updated, databaseFailure(tx.Commit(ctx))
}
func (p *Postgres) AssertLegacyDeviceRetirement(ctx context.Context, w biz.LegacyDeviceRetirementWork) error {
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return databaseFailure(err)
	}
	defer tx.Rollback(ctx)
	q := p.queries.WithTx(tx)
	r, err := p.lockLegacyRetirement(ctx, q, w)
	if err != nil {
		return err
	}
	if !r.Prepared || r.ConfigRv != w.Plan.ConfigRV || r.ConfigUid != w.Plan.ConfigUID || r.ConfigManagedRaw != w.Plan.ManagedRaw || r.DesiredManagedRaw != w.Plan.DesiredManagedRaw || r.MarkerPresent != w.Plan.MarkerPresent || r.MarkerValue != w.Plan.MarkerValue {
		return biz.ErrLeaseLost
	}
	blocked, err := q.LegacyDeviceDependencies(ctx, sqlcgen.LegacyDeviceDependenciesParams{ClusterID: w.ClusterID, ResourceID: w.Retirement.DeviceID})
	if err != nil {
		return databaseFailure(err)
	}
	if blocked > 0 {
		return biz.Fail(biz.ResourceInUse, "product references prevent retirement")
	}
	return nil
}
func (p *Postgres) FinishLegacyDeviceRetirement(ctx context.Context, w biz.LegacyDeviceRetirementWork, o biz.LegacyDeviceRetirementObservation, state string, reason biz.Reason, delay time.Duration) error {
	if state != "closing" && state != "blocked" && state != "retired" {
		return biz.Fail(biz.InvalidArgument, "invalid retirement state")
	}
	cleared := state == "retired"
	if cleared && (!o.Cleared || o.BlockedReason != "" || o.Plan.ConfigUID != w.Retirement.ConfigUID || o.CollectedAt.IsZero() || o.CollectedAt.Before(w.Now) || len(o.Hash) != 64) {
		return biz.Fail(biz.DependencyUnavailable, "confirmed ConfigMap cleanup required")
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return databaseFailure(err)
	}
	defer tx.Rollback(ctx)
	q := p.queries.WithTx(tx)
	r, err := p.lockLegacyRetirement(ctx, q, w)
	if err != nil {
		return err
	}
	if cleared {
		blocked, err := q.LegacyDeviceDependencies(ctx, sqlcgen.LegacyDeviceDependenciesParams{ClusterID: w.ClusterID, ResourceID: w.Retirement.DeviceID})
		if err != nil {
			return databaseFailure(err)
		}
		if blocked > 0 {
			return biz.Fail(biz.ResourceInUse, "new product reference prevents final retirement")
		}
		if err = affected(q.RetireClosedLegacyDevice(ctx, sqlcgen.RetireClosedLegacyDeviceParams{ClusterID: w.ClusterID, ResourceID: r.ResourceID})); err != nil {
			return err
		}
		if err = affected(q.CompleteLegacyDeviceResourceRetirement(ctx, sqlcgen.CompleteLegacyDeviceResourceRetirementParams{ClusterID: w.ClusterID, ResourceID: r.ResourceID, FenceVersion: r.FenceVersion, BindingID: r.BindingID})); err != nil {
			return err
		}
	}
	if err = affected(q.FinishLegacyDeviceRetirementAttempt(ctx, sqlcgen.FinishLegacyDeviceRetirementAttemptParams{ResourceID: r.ResourceID, Version: r.Version, Owner: w.Owner, Epoch: w.Epoch, State: state, Reason: string(reason), Cleared: cleared, DelayMicros: delay.Microseconds()})); err != nil {
		return err
	}
	detail, _ := json.Marshal(map[string]any{"state": state, "reason": reason, "proof_hash": o.Hash, "config_uid": o.Plan.ConfigUID})
	if err = q.InsertLegacyDeviceRetirementHistory(ctx, sqlcgen.InsertLegacyDeviceRetirementHistoryParams{HistoryID: uuid.NewString(), ResourceID: r.ResourceID, OperationID: r.OperationID, Event: "retirement_observed", Detail: detail}); err != nil {
		return databaseFailure(err)
	}
	return databaseFailure(tx.Commit(ctx))
}

// Call after reading/building the exact ConfigMap CAS and immediately before
// dispatch. No ConfigMap refetch may occur after this closing fence check.
func (p *KCProvider) EnsureLegacyDeviceMutationAllowed(ctx context.Context, t biz.ProviderTarget) error {
	d, err := p.repository.queries.GetDeviceAdoption(ctx, sqlcgen.GetDeviceAdoptionParams{ClusterID: p.repository.placement.ClusterID, ResourceID: t.ResourceID})
	if err != nil {
		return readFailure(err)
	}
	if d.RegistrationClosed || d.Retired || d.VirtualSwitchID != nil {
		return switchConflict()
	}
	return nil
}
