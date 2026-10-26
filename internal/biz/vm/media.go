package vm

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"reflect"

	"github.com/google/uuid"
)

// MediaCopy is private to one logical VM. Its source and stable native names
// are frozen at admission; replacement/ejection never deletes an old copy.
type MediaCopy struct {
	ID, SourceDigest string
	Source           Source
	DV, PVC          ObjectRef
	Cleanup          []ObjectRef
	Ready, Tombstone bool
	Reason           string
}

type MediaSlot struct {
	Slot, DesiredCopyID, ObservedCopyID string
	EffectiveNow, PendingRestart        bool
}

type MediaMutation struct {
	UserIntent json.RawMessage `json:"user_intent"`
	Slot       string          `json:"slot,omitempty"`
	Copy       *MediaCopy      `json:"copy,omitempty"`
	BootDevice string          `json:"boot_device,omitempty"`
}

type MediaObservation struct {
	Slots                   []MediaSlot
	Copies                  []MediaCopy
	BootDevice              string
	HasVMI, TemplateMatches bool
}

// These optional capabilities do not expand every existing VM/native double.
type MediaRepository interface {
	AcceptMedia(context.Context, Command, MediaMutation) (Operation, error)
}
type MediaNativePort interface {
	EnsureMediaCopy(context.Context, VM, MediaCopy) (MediaCopy, error)
	ObserveMedia(context.Context, VM) (MediaObservation, error)
	ApplyMedia(context.Context, VM) (MediaObservation, error)
	DeleteMediaCopy(context.Context, VM, MediaCopy) (MediaCopy, bool, error)
}

func mediaAction(action string) bool {
	return action == "set-media" || action == "eject-media" || action == "set-boot"
}
func MediaSourceDigest(source Source) string {
	b, _ := json.Marshal(source)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}
func NewMediaCopy(v VM, source Source) MediaCopy {
	digest := MediaSourceDigest(source)
	id := uuid.NewSHA1(uuid.NameSpaceOID, []byte("ani-vm-media/"+v.TenantID+"/"+v.ID+"/"+digest)).String()
	name := "vm-media-" + id
	return MediaCopy{ID: id, SourceDigest: digest, Source: source, DV: ObjectRef{Kind: "DataVolume", Namespace: v.Spec.Namespace, Name: name}, PVC: ObjectRef{Kind: "PersistentVolumeClaim", Namespace: v.Spec.Namespace, Name: name}}
}
func ValidateMediaSource(v VM, slot string, source Source) error {
	if slot != "INSTALL" && slot != "DRIVER" {
		return ErrInvalid
	}
	kind := "INSTALL_ISO"
	if slot == "DRIVER" {
		kind = "DRIVER_ISO"
	}
	if source.Kind != kind || source.Key == "" || source.PVCName == "" || source.Namespace == "" || source.PVCUID == "" || source.ClusterID != v.Spec.ClusterID || len(source.Checksum) != 64 || source.ContentType != "kubevirt" || (source.VolumeMode != "Filesystem" && source.VolumeMode != "Block") || source.RequestedBytes < 1 {
		return ErrInvalid
	}
	if _, err := hex.DecodeString(source.Checksum); err != nil {
		return ErrInvalid
	}
	original := v.Spec.Media
	if source.OSFamily != original.OSFamily || source.Architecture != original.Architecture {
		return ErrChanged
	}
	// The managed driver ISO can support several Windows versions/devices.
	// Its own version is not the installed OS version and mounting it never
	// changes the VM's frozen firmware, buses, NIC, TPM or UEFI requirements.
	if slot == "INSTALL" && (source.OSVersion != original.OSVersion || source.Firmware != original.Firmware || source.RootBus != original.RootBus || source.DataBus != original.DataBus || source.NICModel != original.NICModel || source.PersistentTPM != original.PersistentTPM || source.PersistentUEFI != original.PersistentUEFI) {
		return ErrChanged
	}
	return nil
}

func mediaSlot(v *VM, slot string) *MediaSlot {
	for i := range v.MediaSlots {
		if v.MediaSlots[i].Slot == slot {
			return &v.MediaSlots[i]
		}
	}
	return nil
}
func FindMediaCopy(v VM, id string) (MediaCopy, bool) {
	for _, copy := range v.MediaCopies {
		if copy.ID == id && !copy.Tombstone {
			return copy, true
		}
	}
	return MediaCopy{}, false
}

// InitializeMedia is idempotent and never resets a later accepted current
// configuration to the original create specification.
func InitializeMedia(v *VM) error {
	if v == nil || !UUID(v.ID) || !UUID(v.TenantID) {
		return ErrInvalid
	}
	if v.CurrentBootDevice != "" {
		return nil
	}
	if len(v.MediaSlots) != 0 || len(v.MediaCopies) != 0 {
		return ErrChanged
	}
	v.CurrentBootDevice = v.Spec.BootDevice
	if v.CurrentBootDevice == "" {
		v.CurrentBootDevice = "SYSTEM_DISK"
	}
	if v.CurrentBootDevice != "SYSTEM_DISK" && v.CurrentBootDevice != "INSTALL_MEDIA" {
		return ErrInvalid
	}
	v.MediaSlots = []MediaSlot{{Slot: "INSTALL"}, {Slot: "DRIVER"}}
	add := func(slot string, source Source) error {
		if err := ValidateMediaSource(*v, slot, source); err != nil {
			return err
		}
		copy := NewMediaCopy(*v, source)
		v.MediaCopies = append(v.MediaCopies, copy)
		mediaSlot(v, slot).DesiredCopyID = copy.ID
		return nil
	}
	if v.Spec.Media.Kind == "INSTALL_ISO" {
		if err := add("INSTALL", v.Spec.Media); err != nil {
			return err
		}
	}
	if v.Spec.Driver != nil {
		if err := add("DRIVER", *v.Spec.Driver); err != nil {
			return err
		}
	}
	return nil
}

func DecodeMediaMutation(raw []byte) (MediaMutation, error) {
	var mutation MediaMutation
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&mutation); err != nil || !json.Valid(mutation.UserIntent) {
		return mutation, ErrInvalid
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return mutation, ErrInvalid
	}
	return mutation, nil
}

// Called inside the existing row-locked acceptance transaction. No native
// objects are created here, and the immutable initial specification is retained.
func ApplyAcceptedMedia(v *VM, o *Operation) error {
	if o == nil || v == nil {
		return ErrInvalid
	}
	if !mediaAction(o.Action) {
		return nil
	}
	if err := InitializeMedia(v); err != nil {
		return err
	}
	mutation, err := DecodeMediaMutation(o.Intent)
	if err != nil {
		return err
	}
	switch o.Action {
	case "set-media":
		if mutation.Copy == nil || mutation.BootDevice != "" {
			return ErrInvalid
		}
		if err = ValidateMediaSource(*v, mutation.Slot, mutation.Copy.Source); err != nil {
			return err
		}
		planned := NewMediaCopy(*v, mutation.Copy.Source)
		if mutation.Copy.ID != planned.ID || mutation.Copy.SourceDigest != planned.SourceDigest || mutation.Copy.DV.Name != planned.DV.Name || mutation.Copy.PVC.Name != planned.PVC.Name {
			return ErrChanged
		}
		copy, found := FindMediaCopy(*v, planned.ID)
		if found {
			if !reflect.DeepEqual(copy.Source, mutation.Copy.Source) {
				return ErrChanged
			}
		} else {
			v.MediaCopies = append(v.MediaCopies, planned)
		}
		slot := mediaSlot(v, mutation.Slot)
		if slot == nil {
			return ErrInvalid
		}
		slot.DesiredCopyID = planned.ID
	case "eject-media":
		if mutation.Copy != nil || mutation.BootDevice != "" {
			return ErrInvalid
		}
		slot := mediaSlot(v, mutation.Slot)
		if slot == nil {
			return ErrInvalid
		}
		slot.DesiredCopyID = ""
	case "set-boot":
		if mutation.Copy != nil || mutation.Slot != "" || (mutation.BootDevice != "SYSTEM_DISK" && mutation.BootDevice != "INSTALL_MEDIA") {
			return ErrInvalid
		}
		if mutation.BootDevice == "INSTALL_MEDIA" && mediaSlot(v, "INSTALL").DesiredCopyID == "" {
			return ErrInvalid
		}
		v.CurrentBootDevice = mutation.BootDevice
	}
	return nil
}

func (w *Worker) ensureDesiredMediaCopies(ctx context.Context, o *Operation, v *VM) (bool, error) {
	if err := InitializeMedia(v); err != nil {
		return false, err
	}
	native, ok := w.native.(MediaNativePort)
	for _, slot := range v.MediaSlots {
		if slot.DesiredCopyID == "" {
			continue
		}
		if !ok {
			return false, ErrDependency
		}
		for i := range v.MediaCopies {
			copy := v.MediaCopies[i]
			if copy.ID != slot.DesiredCopyID || copy.Tombstone {
				continue
			}
			if err := w.checkpoint(ctx, o, *v, "ensure-media-copy", copy.DV); err != nil {
				return false, err
			}
			observed, err := native.EnsureMediaCopy(ctx, *v, copy)
			if err != nil {
				return false, err
			}
			o.Pending = nil
			if !reflect.DeepEqual(copy, observed) {
				v.MediaCopies[i] = observed
				return true, nil
			}
		}
	}
	return false, nil
}
func applyMediaObservation(v *VM, observed MediaObservation) {
	v.MediaSlots = observed.Slots
	if observed.Copies != nil {
		v.MediaCopies = observed.Copies
	}
}
func (w *Worker) media(ctx context.Context, o *Operation, v *VM) error {
	native, ok := w.native.(MediaNativePort)
	if !ok {
		return ErrDependency
	}
	if _, err := DecodeMediaMutation(o.Intent); err != nil {
		return err
	}
	changed, err := w.ensureDesiredMediaCopies(ctx, o, v)
	if err != nil || changed {
		return err
	}
	if err = w.checkpoint(ctx, o, *v, "apply-media-template", v.Native); err != nil {
		return err
	}
	observed, err := native.ApplyMedia(ctx, *v)
	if err != nil {
		return err
	}
	if !observed.TemplateMatches {
		return ErrDependency
	}
	applyMediaObservation(v, observed)
	o.State = "succeeded"
	o.Pending = nil
	return nil
}

// After native Start/Stop observation this refreshes actual VMI media without
// resolving the mutable catalog or waiting for a restart operation.
func (w *Worker) observeMedia(ctx context.Context, v *VM) error {
	native, ok := w.native.(MediaNativePort)
	if !ok || len(v.MediaSlots) == 0 {
		return nil
	}
	observed, err := native.ObserveMedia(ctx, *v)
	if err != nil {
		return err
	}
	applyMediaObservation(v, observed)
	return nil
}
func (w *Worker) removeMedia(ctx context.Context, o *Operation, v *VM) (bool, error) {
	native, ok := w.native.(MediaNativePort)
	for i := range v.MediaCopies {
		copy := v.MediaCopies[i]
		if copy.Tombstone {
			continue
		}
		if !ok {
			return false, ErrDependency
		}
		if err := w.checkpoint(ctx, o, *v, "delete-media-copy", copy.DV); err != nil {
			return false, err
		}
		observed, done, err := native.DeleteMediaCopy(ctx, *v, copy)
		if err != nil {
			return false, err
		}
		v.MediaCopies[i] = observed
		o.Pending = nil
		if done {
			v.MediaCopies[i].Tombstone = true
		}
		return false, nil
	}
	return true, nil
}
