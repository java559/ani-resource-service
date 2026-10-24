package vm

import (
	"context"
	"testing"

	"github.com/google/uuid"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/vm/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type snapshotServiceRepo struct {
	biz.Repository
	current        biz.VM
	reads, accepts int
	replay         *biz.Operation
	digest         string
}

func (r *snapshotServiceRepo) Get(_ context.Context, tenant, id string) (biz.VM, error) {
	r.reads++
	if r.current.TenantID != tenant || r.current.ID != id {
		return biz.VM{}, biz.ErrNotFound
	}
	return r.current, nil
}
func (r *snapshotServiceRepo) LookupReplay(_ context.Context, c biz.Command) (biz.Operation, error) {
	if r.replay == nil {
		return biz.Operation{}, biz.ErrNotFound
	}
	if c.RequestDigest != r.digest {
		return biz.Operation{}, biz.ErrIdempotency
	}
	return *r.replay, nil
}
func (r *snapshotServiceRepo) AcceptCommand(_ context.Context, c biz.Command) (biz.Operation, error) {
	r.accepts++
	return biz.Operation{ID: uuid.NewString(), VMID: c.VMID, Action: c.Action, IntentVersion: c.ExpectedVersion + 1, State: "accepted"}, nil
}

type snapshotServiceNative struct {
	biz.NativePort
	fresh   biz.Snapshot
	reads   int
	running bool
}

func (n *snapshotServiceNative) Observe(_ context.Context, v biz.VM) (biz.Observation, error) {
	result := biz.Observation{VM: v.Native, Stopped: !n.running, Running: n.running}
	if n.running {
		result.VMI = &biz.ObjectRef{UID: "running-vmi"}
	}
	return result, nil
}
func (n *snapshotServiceNative) PrepareSnapshot(_ context.Context, _ biz.VM, s biz.Snapshot) (biz.Snapshot, error) {
	return s, nil
}
func (n *snapshotServiceNative) EnsureSnapshot(context.Context, biz.VM, biz.Snapshot) (biz.Snapshot, error) {
	panic("read/acceptance created native snapshot")
}
func (n *snapshotServiceNative) ObserveSnapshot(context.Context, biz.VM, biz.Snapshot) (biz.Snapshot, error) {
	n.reads++
	return n.fresh, nil
}
func (n *snapshotServiceNative) DeleteSnapshot(context.Context, biz.VM, biz.Snapshot) (bool, error) {
	panic("read/acceptance deleted native snapshot")
}
func (n *snapshotServiceNative) EnsureRestore(context.Context, biz.VM, biz.Snapshot, biz.ObjectRef) (biz.RestoreObservation, error) {
	panic("read/acceptance created native restore")
}
func (n *snapshotServiceNative) ApplyRestoredConfiguration(context.Context, biz.VM, biz.Snapshot) (bool, error) {
	panic("read/acceptance changed native configuration")
}
func serviceSnapshotFixture(t *testing.T) (*Service, *snapshotServiceRepo, *snapshotServiceNative, context.Context, biz.Snapshot) {
	t.Helper()
	tenant, id := uuid.NewString(), uuid.NewString()
	v := biz.VM{TenantID: tenant, ID: id, Version: 4, Native: biz.ObjectRef{Kind: "VirtualMachine", Name: "vm-a", Namespace: "tenant-a", UID: "vm-uid"}, Spec: biz.Spec{Namespace: "tenant-a", BootDevice: "SYSTEM_DISK"}, CurrentBootDevice: "SYSTEM_DISK"}
	d := biz.NewInitialDisk(id, "system", "system", "system", 8)
	d.DV.Namespace = v.Spec.Namespace
	d.DV.UID = "dv-uid"
	d.PVC = biz.ObjectRef{Kind: "PersistentVolumeClaim", Namespace: v.Spec.Namespace, Name: d.DV.Name, UID: "pvc-uid"}
	v.Disks = []biz.Disk{d}
	s, err := biz.NewSnapshot(v, uuid.NewString(), "snapshot")
	if err != nil {
		t.Fatal(err)
	}
	s.Native.UID = "snapshot-uid"
	s.Content = biz.ObjectRef{Kind: "VirtualMachineSnapshotContent", Namespace: v.Spec.Namespace, Name: "content", UID: "content-uid"}
	s.Ready = true
	s.Included = []string{d.VolumeName}
	s.Complete = biz.SnapshotComplete(s)
	v.Snapshots = []biz.Snapshot{s}
	repo := &snapshotServiceRepo{current: v}
	native := &snapshotServiceNative{fresh: s}
	service := &Service{repo: repo, native: native}
	ctx := biz.WithCaller(context.Background(), biz.Caller{TenantID: tenant, Actor: "user:1"})
	return service, repo, native, ctx, s
}
func TestSnapshotServiceReadsAreScopedAndPreviewIsSideEffectFree(t *testing.T) {
	service, repo, native, ctx, s := serviceSnapshotFixture(t)
	request := &pb.GetSnapshotRequest{TenantId: repo.current.TenantID, VmId: repo.current.ID, SnapshotId: s.ID}
	result, err := service.GetSnapshot(ctx, request)
	if err != nil || !result.Ready || result.Completeness != "COMPLETE" || len(result.Disks) != 1 {
		t.Fatal(result, err)
	}
	request.TenantId = uuid.NewString()
	reads := native.reads
	if _, err = service.GetSnapshot(ctx, request); status.Code(err) != codes.PermissionDenied || native.reads != reads {
		t.Fatal("cross tenant reached native", err)
	}
	request.TenantId = repo.current.TenantID
	request.SnapshotId = uuid.NewString()
	if _, err = service.GetSnapshot(ctx, request); status.Code(err) != codes.NotFound {
		t.Fatal(err)
	}
	preview, err := service.PreviewRestore(ctx, &pb.PreviewRestoreRequest{TenantId: repo.current.TenantID, VmId: repo.current.ID, SnapshotId: s.ID})
	if err != nil || preview.Version != 4 || preview.SnapshotUid != s.Native.UID || len(preview.PreviewDigest) != 64 || repo.accepts != 0 {
		t.Fatal(preview, err)
	}
	native.running = true
	if _, err = service.PreviewRestore(ctx, &pb.PreviewRestoreRequest{TenantId: repo.current.TenantID, VmId: repo.current.ID, SnapshotId: s.ID}); status.Code(err) != codes.FailedPrecondition {
		t.Fatal("running target preview admitted", err)
	}
}
func TestRestoreAdmissionRechecksFreshNativeSnapshotBeforeAccept(t *testing.T) {
	service, repo, native, ctx, s := serviceSnapshotFixture(t)
	preview, err := service.PreviewRestore(ctx, &pb.PreviewRestoreRequest{TenantId: repo.current.TenantID, VmId: repo.current.ID, SnapshotId: s.ID})
	if err != nil {
		t.Fatal(err)
	}
	request := &pb.RestoreSnapshotRequest{TenantId: repo.current.TenantID, VmId: repo.current.ID, SnapshotId: s.ID, ExpectedVersion: 4, IdempotencyKey: "restore-key", PreviewDigest: preview.PreviewDigest, DestructiveConfirmation: true}
	native.fresh.Content.UID = "replacement-content"
	if _, err = service.RestoreSnapshot(ctx, request); status.Code(err) != codes.FailedPrecondition || repo.accepts != 0 {
		t.Fatal("fresh native snapshot mismatch reached acceptance", err)
	}
	native.fresh = s
	request.DestructiveConfirmation = false
	if _, err = service.RestoreSnapshot(ctx, request); status.Code(err) != codes.InvalidArgument || repo.accepts != 0 {
		t.Fatal("destructive confirmation missing", err)
	}
	request.DestructiveConfirmation = true
	operation, err := service.RestoreSnapshot(ctx, request)
	if err != nil || operation.Id == "" || repo.accepts != 1 {
		t.Fatal(operation, err)
	}
	raw, _ := pb.CanonicalCommandIntent(request)
	repo.replay = &biz.Operation{ID: operation.Id, VMID: repo.current.ID, Action: "restore-snapshot", State: "running"}
	repo.digest = pb.Digest(raw)
	reads := native.reads
	repo.current.Version = 99
	repo.current.Closing = true
	native.running = true
	if replayed, err := service.RestoreSnapshot(ctx, request); err != nil || replayed.Id != operation.Id || native.reads != reads || repo.accepts != 1 {
		t.Fatal("original replay reopened native authority or changed operation", replayed, err)
	}
}
