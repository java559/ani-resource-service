//go:build instanceintegration

package vm

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	"github.com/zhangzhe-ctrl/ani-resource-service/tests/testenv"
)

func TestMediaPostgresFrozenSourceReplayCurrentConfigurationAndClosing(t *testing.T) {
	ctx := context.Background()
	db := testenv.NewDatabase(t)
	if e := Migrate(ctx, db.Owner.Config().ConnString(), db.RuntimeRole); e != nil {
		t.Fatal(e)
	}
	repository, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer func() { repository.Close() }()
	tenant, id, createID := uuid.NewString(), uuid.NewString(), uuid.NewString()
	source := biz.Source{Key: "ubuntu-install", Kind: "INSTALL_ISO", ClusterID: uuid.NewString(), Namespace: "media", PVCName: "install", PVCUID: uuid.NewString(), Checksum: strings.Repeat("a", 64), RequestedBytes: 2 << 30, VirtualBytes: 1 << 30, VolumeMode: "Filesystem", ContentType: "kubevirt", OSFamily: "linux", OSVersion: "24.04", Architecture: "amd64", Firmware: "uefi", RootBus: "virtio", DataBus: "scsi", NICModel: "virtio"}
	spec := biz.Spec{Canonical: []byte(`{"immutable":"initial ISO create"}`), Digest: strings.Repeat("b", 64), Namespace: "tenant-media", NamespaceUID: uuid.NewString(), ClusterID: source.ClusterID, Media: source, BootDevice: "INSTALL_MEDIA"}
	if _, e = repository.AcceptCreate(ctx, biz.Acceptance{TenantID: tenant, VMID: id, OperationID: createID, RequestDigest: strings.Repeat("c", 64), Spec: spec}); e != nil {
		t.Fatal(e)
	}
	finish := func(want string) biz.VM {
		t.Helper()
		operation, current, e := repository.Claim(ctx, uuid.NewString(), time.Minute)
		if e != nil || operation.ID != want {
			t.Fatalf("claim %s: %+v %v", want, operation, e)
		}
		operation.State = "succeeded"
		if e = repository.Save(ctx, operation, current); e != nil {
			t.Fatal(e)
		}
		return current
	}
	current := finish(createID)
	if len(current.MediaCopies) != 1 || len(current.MediaSlots) != 2 {
		t.Fatal("initial copy identity was not durable before native execution")
	}
	originalCanonical := append([]byte(nil), current.Spec.Canonical...)
	originalCopy := current.MediaCopies[0]
	var originalSpecJSON string
	if e = repository.pool.QueryRow(ctx, "SELECT initial_spec::text FROM vm.virtual_machines WHERE tenant_id=$1 AND id=$2", tenant, id).Scan(&originalSpecJSON); e != nil {
		t.Fatal(e)
	}
	replacement := source
	replacement.Key = "ubuntu-install-new"
	replacement.PVCUID = uuid.NewString()
	replacement.Checksum = strings.Repeat("d", 64)
	copy := biz.NewMediaCopy(current, replacement)
	command := biz.Command{TenantID: tenant, VMID: id, Action: "set-media", Key: "opaque-media-key", ExpectedVersion: 1, RequestDigest: strings.Repeat("e", 64), Intent: []byte(`{"slot":"INSTALL","media_key":"ubuntu-install-new","expected_version":"1"}`)}
	first, e := repository.AcceptMedia(ctx, command, biz.MediaMutation{Slot: "INSTALL", Copy: &copy})
	if e != nil {
		t.Fatal(e)
	}
	current, e = repository.Get(ctx, tenant, id)
	if e != nil {
		t.Fatal(e)
	}
	if current.Version != 2 || len(current.MediaCopies) != 2 || current.MediaSlots[0].DesiredCopyID != copy.ID {
		t.Fatalf("current media configuration lost: version=%d copies=%d slots=%+v expected_copy=%s", current.Version, len(current.MediaCopies), current.MediaSlots, copy.ID)
	}
	if !bytes.Equal(current.Spec.Canonical, originalCanonical) || current.Spec.Media != source || current.Spec.Digest != spec.Digest || current.Spec.BootDevice != spec.BootDevice {
		t.Fatalf("immutable create specification changed: canonical_equal=%t full_source_equal=%t digest_equal=%t boot=%s", bytes.Equal(current.Spec.Canonical, originalCanonical), current.Spec.Media == source, current.Spec.Digest == spec.Digest, current.Spec.BootDevice)
	}
	if current.MediaCopies[0].ID != originalCopy.ID || current.MediaCopies[0].Source != source || current.MediaCopies[1].ID != copy.ID || current.MediaCopies[1].Source != replacement {
		t.Fatal("old or newly frozen copy identity/source changed")
	}
	var afterSpecJSON string
	if e = repository.pool.QueryRow(ctx, "SELECT initial_spec::text FROM vm.virtual_machines WHERE tenant_id=$1 AND id=$2", tenant, id).Scan(&afterSpecJSON); e != nil || afterSpecJSON != originalSpecJSON {
		t.Fatal("initial_spec persistent document was rewritten", e)
	}
	stored, e := biz.DecodeMediaMutation(first.Intent)
	if e != nil || stored.Copy.Source.PVCUID != replacement.PVCUID {
		t.Fatal("operation source was not frozen", e)
	}
	repository.Close()
	repository, e = OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	// A restarted peer supplies a changed catalog under the original public
	// key: the existing operation wins before re-freezing that new source.
	changed := copy
	changed.Source.PVCUID = uuid.NewString()
	changed.SourceDigest = biz.MediaSourceDigest(changed.Source)
	replayed, e := repository.AcceptMedia(ctx, command, biz.MediaMutation{Slot: "INSTALL", Copy: &changed})
	frozenReplay, decodeError := biz.DecodeMediaMutation(replayed.Intent)
	if e != nil || decodeError != nil || replayed.ID != first.ID || replayed.RequestDigest != first.RequestDigest || frozenReplay.Copy == nil || frozenReplay.Copy.Source != stored.Copy.Source || frozenReplay.Copy.ID != stored.Copy.ID || frozenReplay.Copy.DV.Name != stored.Copy.DV.Name {
		t.Fatal("catalog change altered accepted replay after repository restart", e, decodeError)
	}
	different := command
	different.RequestDigest = strings.Repeat("f", 64)
	different.ExpectedVersion = 2
	if _, e = repository.AcceptMedia(ctx, different, biz.MediaMutation{Slot: "INSTALL", Copy: &changed}); !errors.Is(e, biz.ErrIdempotency) {
		t.Fatal("same key changed request accepted", e)
	}
	if _, e = repository.Get(ctx, uuid.NewString(), id); !errors.Is(e, biz.ErrNotFound) {
		t.Fatal("cross-tenant media VM visible", e)
	}
	finish(first.ID)
	eject := biz.Command{TenantID: tenant, VMID: id, Action: "eject-media", Key: "eject-key", ExpectedVersion: 2, RequestDigest: strings.Repeat("1", 64), Intent: []byte(`{"slot":"INSTALL","expected_version":"2"}`)}
	ejected, e := repository.AcceptMedia(ctx, eject, biz.MediaMutation{Slot: "INSTALL"})
	if e != nil {
		t.Fatal(e)
	}
	finish(ejected.ID)
	current, e = repository.Get(ctx, tenant, id)
	if e != nil || len(current.MediaCopies) != 2 || current.MediaSlots[0].DesiredCopyID != "" {
		t.Fatal("ejection destroyed retained inventory", e)
	}
	deleted, e := repository.AcceptDelete(ctx, biz.Acceptance{TenantID: tenant, VMID: id, OperationID: uuid.NewString(), CreateOperationID: createID, ExpectedVersion: 3, RequestDigest: strings.Repeat("2", 64)})
	if e != nil {
		t.Fatal(e)
	}
	if replayed, e = repository.AcceptMedia(ctx, command, biz.MediaMutation{Slot: "INSTALL", Copy: &changed}); e != nil || replayed.ID != first.ID {
		t.Fatal("closing lost original replay", e)
	}
	eject.Key = "new-after-closing"
	eject.ExpectedVersion = 4
	if _, e = repository.AcceptMedia(ctx, eject, biz.MediaMutation{Slot: "INSTALL"}); !errors.Is(e, biz.ErrBusy) {
		t.Fatal("new media intent admitted after closing", e)
	}
	var count int
	var original json.RawMessage
	if e = repository.pool.QueryRow(ctx, "SELECT count(*) FROM vm.operations WHERE tenant_id=$1 AND vm_id=$2", tenant, id).Scan(&count); e != nil || count != 4 {
		t.Fatal("unexpected media mutation side effect", count, e)
	}
	if e = repository.pool.QueryRow(ctx, "SELECT initial_spec FROM vm.virtual_machines WHERE tenant_id=$1 AND id=$2", tenant, id).Scan(&original); e != nil {
		t.Fatal(e)
	}
	var saved biz.Spec
	if e = json.Unmarshal(original, &saved); e != nil || saved.Media.PVCUID != source.PVCUID {
		t.Fatal("initial spec was rewritten", e)
	}
	if deleted.Action != biz.DeleteAction {
		t.Fatal("delete association changed")
	}
}
