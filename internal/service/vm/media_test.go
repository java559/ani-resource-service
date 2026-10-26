package vm

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/vm/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

type mediaServiceRepo struct {
	biz.Repository
	current        biz.VM
	replay         *biz.Operation
	replayDigest   string
	reads, accepts int
	frozen         biz.MediaMutation
}

func (r *mediaServiceRepo) LookupReplay(_ context.Context, c biz.Command) (biz.Operation, error) {
	if r.replay != nil {
		if r.replayDigest != "" && r.replayDigest != c.RequestDigest {
			return biz.Operation{}, biz.ErrIdempotency
		}
		return *r.replay, nil
	}
	return biz.Operation{}, biz.ErrNotFound
}
func (r *mediaServiceRepo) Get(_ context.Context, tenant, id string) (biz.VM, error) {
	r.reads++
	if tenant != r.current.TenantID || id != r.current.ID {
		return biz.VM{}, biz.ErrNotFound
	}
	return r.current, nil
}
func (r *mediaServiceRepo) AcceptMedia(_ context.Context, c biz.Command, mutation biz.MediaMutation) (biz.Operation, error) {
	r.accepts++
	r.frozen = mutation
	return biz.Operation{ID: uuid.NewString(), VMID: c.VMID, Action: c.Action, IntentVersion: c.ExpectedVersion + 1, State: "accepted"}, nil
}

type mediaServiceNative struct {
	biz.NativePort
	validated int
}

func (n *mediaServiceNative) ValidateSource(context.Context, biz.Source) error {
	n.validated++
	return nil
}
func serviceMediaFixture() (*Service, *mediaServiceRepo, *mediaServiceNative, context.Context, *pb.SetMediaRequest) {
	tenant, id := uuid.NewString(), uuid.NewString()
	cluster := uuid.NewString()
	source := biz.Source{Key: "ubuntu-install", Kind: "INSTALL_ISO", ClusterID: cluster, Namespace: "media", PVCName: "iso", PVCUID: uuid.NewString(), Checksum: strings.Repeat("a", 64), RequestedBytes: 2 << 30, VirtualBytes: 1 << 30, VolumeMode: "Filesystem", ContentType: "kubevirt", OSFamily: "linux", OSVersion: "24.04", Architecture: "amd64", Firmware: "uefi", RootBus: "virtio", DataBus: "scsi", NICModel: "virtio"}
	repo := &mediaServiceRepo{current: biz.VM{TenantID: tenant, ID: id, Version: 1, Spec: biz.Spec{ClusterID: cluster, Namespace: "tenant-test", Media: source, BootDevice: "INSTALL_MEDIA"}}}
	native := &mediaServiceNative{}
	ref := &pb.MediaReference{MediaKey: source.Key, Kind: source.Kind, ClusterId: source.ClusterID, Namespace: source.Namespace, PvcName: source.PVCName, PvcUid: source.PVCUID, Checksum: source.Checksum, RequestedBytes: source.RequestedBytes, VirtualCapacityBytes: source.VirtualBytes, VolumeMode: source.VolumeMode, ContentType: source.ContentType, OsFamily: source.OSFamily, OsVersion: source.OSVersion, Architecture: source.Architecture, Firmware: source.Firmware, RootBus: source.RootBus, DataBus: source.DataBus, NicModel: source.NICModel}
	service := &Service{repo: repo, native: native, media: map[string]*pb.MediaReference{ref.MediaKey: ref}}
	ctx := biz.WithCaller(context.Background(), biz.Caller{TenantID: tenant, Actor: "user:1"})
	return service, repo, native, ctx, &pb.SetMediaRequest{TenantId: tenant, VmId: id, IdempotencyKey: "opaque-media-key", ExpectedVersion: 1, Slot: "INSTALL", MediaKey: ref.MediaKey}
}
func TestMediaServiceFreezesSourceAndReplaysBeforeCatalogOrVersion(t *testing.T) {
	service, repo, native, ctx, request := serviceMediaFixture()
	operation, e := service.SetMedia(ctx, request)
	if e != nil || operation.Id == "" || repo.accepts != 1 || native.validated != 1 || repo.frozen.Copy == nil || repo.frozen.Copy.Source.PVCUID != service.media[request.MediaKey].PvcUid {
		t.Fatal("source was not frozen before acceptance", operation, e)
	}
	raw, _ := pb.CanonicalCommandIntent(request)
	stored := biz.Operation{ID: operation.Id, VMID: request.VmId, Action: "set-media", IntentVersion: 2, State: "succeeded"}
	repo.replay = &stored
	repo.replayDigest = pb.Digest(raw)
	service.media = nil
	repo.current.Version = 10
	repo.current.Closing = true
	reads := repo.reads
	replayed, e := service.SetMedia(ctx, request)
	if e != nil || replayed.Id != operation.Id || repo.accepts != 1 || native.validated != 1 || repo.reads != reads {
		t.Fatal("original replay re-resolved removed catalog or current state", replayed, e)
	}
	request.ExpectedVersion = 10
	if _, e = service.SetMedia(ctx, request); e == nil {
		t.Fatal("same key changed expected version was accepted")
	}
}
func TestMediaServiceRegisteredRetainedCopyDoesNotRecloneSource(t *testing.T) {
	service, repo, native, ctx, request := serviceMediaFixture()
	if e := biz.InitializeMedia(&repo.current); e != nil {
		t.Fatal(e)
	}
	repo.current.MediaCopies[0].DV.UID = uuid.NewString()
	service.media = nil
	if _, e := service.SetMedia(ctx, request); e != nil || native.validated != 0 || repo.frozen.Copy.DV.UID != repo.current.MediaCopies[0].DV.UID {
		t.Fatal("registered retained copy was not reused", e)
	}
}
