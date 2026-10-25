package vm

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func mediaFixtureVM() VM {
	id := uuid.NewString()
	source := Source{Key: "linux-install", Kind: "INSTALL_ISO", ClusterID: uuid.NewString(), Namespace: "media", PVCName: "linux-iso", PVCUID: uuid.NewString(), Checksum: strings.Repeat("a", 64), VolumeMode: "Filesystem", ContentType: "kubevirt", RequestedBytes: 2 << 30, VirtualBytes: 1 << 30, OSFamily: "linux", OSVersion: "24.04", Architecture: "amd64", Firmware: "uefi", RootBus: "virtio", DataBus: "scsi", NICModel: "virtio"}
	return VM{TenantID: uuid.NewString(), ID: id, Version: 1, Native: ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-test", Name: "vm-" + id, UID: uuid.NewString()}, Spec: Spec{Canonical: json.RawMessage(`{"frozen":"initial"}`), ClusterID: source.ClusterID, Namespace: "tenant-test", NamespaceUID: uuid.NewString(), Media: source, BootDevice: "INSTALL_MEDIA"}}
}
func mediaTestOperation(t *testing.T, action string, mutation MediaMutation) Operation {
	t.Helper()
	mutation.UserIntent = json.RawMessage(`{"original":"user intent"}`)
	raw, e := json.Marshal(mutation)
	if e != nil {
		t.Fatal(e)
	}
	return Operation{Action: action, Intent: raw, State: "running"}
}
func TestMediaAcceptedReplacementEjectionRetainsCopiesAndInitialSpec(t *testing.T) {
	v := mediaFixtureVM()
	if e := InitializeMedia(&v); e != nil {
		t.Fatal(e)
	}
	first := v.MediaCopies[0]
	initial := string(v.Spec.Canonical)
	second := v.Spec.Media
	second.Key = "replacement"
	second.PVCUID = uuid.NewString()
	second.Checksum = strings.Repeat("b", 64)
	copy := NewMediaCopy(v, second)
	op := mediaTestOperation(t, "set-media", MediaMutation{Slot: "INSTALL", Copy: &copy})
	if e := ApplyAcceptedMedia(&v, &op); e != nil {
		t.Fatal(e)
	}
	if len(v.MediaCopies) != 2 || v.MediaSlots[0].DesiredCopyID != copy.ID || v.MediaCopies[0].ID != first.ID {
		t.Fatal("replacement lost a retained copy")
	}
	eject := mediaTestOperation(t, "eject-media", MediaMutation{Slot: "INSTALL"})
	if e := ApplyAcceptedMedia(&v, &eject); e != nil {
		t.Fatal(e)
	}
	boot := mediaTestOperation(t, "set-boot", MediaMutation{BootDevice: "SYSTEM_DISK"})
	if e := ApplyAcceptedMedia(&v, &boot); e != nil {
		t.Fatal(e)
	}
	if e := InitializeMedia(&v); e != nil {
		t.Fatal(e)
	}
	if v.CurrentBootDevice != "SYSTEM_DISK" || v.MediaSlots[0].DesiredCopyID != "" || len(v.MediaCopies) != 2 || string(v.Spec.Canonical) != initial || v.Spec.BootDevice != "INSTALL_MEDIA" {
		t.Fatal("current mutation changed immutable create spec or reinitialized ejected media")
	}
	// Same public key replay is handled before this hook. Reapplying a frozen
	// accepted configuration is still idempotent and does not append a copy.
	if e := ApplyAcceptedMedia(&v, &op); e != nil || len(v.MediaCopies) != 2 {
		t.Fatal("frozen copy identity changed", e)
	}
}
func TestMediaCompatibilityPreservesWindowsRequirements(t *testing.T) {
	v := mediaFixtureVM()
	v.Spec.Media.OSFamily = "windows"
	v.Spec.Media.OSVersion = "Windows 11"
	v.Spec.Media.PersistentTPM = true
	v.Spec.Media.PersistentUEFI = true
	driver := v.Spec.Media
	driver.Kind = "DRIVER_ISO"
	driver.Key = "virtio-win"
	driver.OSVersion = "virtio-win-0.1.285"
	driver.PersistentTPM = false
	driver.PersistentUEFI = false
	if e := ValidateMediaSource(v, "DRIVER", driver); e != nil {
		t.Fatal("managed Windows driver may span OS versions", e)
	}
	if !v.Spec.Media.PersistentTPM || !v.Spec.Media.PersistentUEFI {
		t.Fatal("driver changed frozen OS requirements")
	}
	for _, change := range []func(*Source){func(s *Source) { s.OSFamily = "linux" }, func(s *Source) { s.Architecture = "arm64" }, func(s *Source) { s.Firmware = "bios" }, func(s *Source) { s.PersistentTPM = false }} {
		wrong := v.Spec.Media
		change(&wrong)
		if e := ValidateMediaSource(v, "INSTALL", wrong); !errors.Is(e, ErrChanged) {
			t.Fatalf("incompatible installation accepted: %v", e)
		}
	}
	wrong := driver
	wrong.Kind = "DISK_IMAGE"
	if e := ValidateMediaSource(v, "DRIVER", wrong); !errors.Is(e, ErrInvalid) {
		t.Fatal("writable image accepted as optical media", e)
	}
}

type mediaCheckpointRepo struct {
	Repository
	checkpoints int
}

func (r *mediaCheckpointRepo) Checkpoint(context.Context, Operation, VM) error {
	r.checkpoints++
	return nil
}

type mediaWorkerNative struct {
	NativePort
	applied, removed int
}

func (*mediaWorkerNative) EnsureMediaCopy(_ context.Context, _ VM, copy MediaCopy) (MediaCopy, error) {
	if copy.DV.UID == "" {
		copy.DV.UID = uuid.NewString()
	}
	return copy, nil
}
func (*mediaWorkerNative) ObserveMedia(_ context.Context, v VM) (MediaObservation, error) {
	return MediaObservation{Slots: v.MediaSlots, TemplateMatches: true}, nil
}
func (n *mediaWorkerNative) ApplyMedia(_ context.Context, v VM) (MediaObservation, error) {
	n.applied++
	slots := append([]MediaSlot(nil), v.MediaSlots...)
	slots[0].ObservedCopyID = ""
	slots[0].PendingRestart = true
	slots[0].EffectiveNow = false
	return MediaObservation{Slots: slots, TemplateMatches: true, HasVMI: true}, nil
}
func (n *mediaWorkerNative) DeleteMediaCopy(_ context.Context, _ VM, copy MediaCopy) (MediaCopy, bool, error) {
	n.removed++
	return copy, true, nil
}
func TestMediaTemplateCompletionDoesNotWaitForRestartOrDeleteRetainedCopies(t *testing.T) {
	v := mediaFixtureVM()
	if e := InitializeMedia(&v); e != nil {
		t.Fatal(e)
	}
	copy := v.MediaCopies[0]
	op := mediaTestOperation(t, "set-media", MediaMutation{Slot: "INSTALL", Copy: &copy})
	repo := &mediaCheckpointRepo{}
	native := &mediaWorkerNative{}
	worker := &Worker{repo: repo, native: native}
	if e := worker.media(context.Background(), &op, &v); e != nil {
		t.Fatal(e)
	}
	if op.State == "succeeded" {
		t.Fatal("copy UID was not checkpointed first")
	}
	if e := worker.media(context.Background(), &op, &v); e != nil {
		t.Fatal(e)
	}
	if op.State != "succeeded" || native.applied != 1 || !v.MediaSlots[0].PendingRestart || v.MediaSlots[0].EffectiveNow || len(v.MediaCopies) != 1 {
		t.Fatal("persistent config should complete with accurate pending restart")
	}
	v.Closing = true
	for i := 0; i < 3; i++ {
		done, e := worker.removeMedia(context.Background(), &op, &v)
		if e != nil {
			t.Fatal(e)
		}
		if done {
			break
		}
	}
	if native.removed != 1 || !v.MediaCopies[0].Tombstone {
		t.Fatal("retained copy not included in final cleanup")
	}
}
