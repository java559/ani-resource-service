package vm

import "context"

// BackendState records a native persistent firmware/TPM volume. It is not a
// user disk and is never created or restored by the Resource service.
type BackendState struct {
	PVC, PV, Owner                              ObjectRef
	VolumeName, StorageClass, VolumeMode, Phase string
	ReclaimPolicy, CSIDriver, VolumeHandle      string
	RequestedBytes, CapacityBytes               int64
	Current, Present                            bool
}

func NeedsBackendState(s Spec) bool {
	return s.Media.PersistentTPM || s.Media.PersistentUEFI
}

// BackendNativePort is optional for existing native consumers. Observation
// retains disappeared and replaced identities until reclamation is confirmed.
type BackendNativePort interface {
	ValidateBackendConfiguration(context.Context, Spec) error
	ObserveBackendState(context.Context, VM, []BackendState) ([]BackendState, error)
	BackendStateRemoved(context.Context, VM, BackendState) (bool, error)
}
