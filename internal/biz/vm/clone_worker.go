package vm

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// Clone worker steps. They are stored in the existing Operation.Step field; no
// new public state schema is introduced.
const (
	cloneStepSubmit = "clone-submit"
	cloneStepDelete = "clone-delete"
	cloneStepFail   = "clone-failed-cleanup"
)

// cloneRequest builds the fixed native submission identity of this target.
func cloneRequest(v *VM) CloneRequest {
	cp := v.CloneProgress
	return CloneRequest{
		Name:         cp.Name,
		OperationID:  v.CreateOperationID,
		TargetName:   v.Native.Name,
		SMBiosSerial: v.ID,
		Source:       v.Spec.CloneSource.Native,
	}
}

// clone is the CreateAction branch for VM_CLONE. It replaces the image create
// steps (ValidateSource/EnsureVM/EnsureInitialization/EnsureVolume) entirely.
func (w *Worker) clone(ctx context.Context, o *Operation, v *VM) error {
	native, ok := w.native.(CloneNativePort)
	if !ok {
		return ErrDependency
	}
	sources, ok := w.repo.(CloneSourcePort)
	if !ok {
		return ErrDependency
	}
	if v.Spec.CloneSource == nil {
		return ErrInvalid
	}
	if v.CloneProgress == nil {
		v.CloneProgress = &CloneProgress{Name: CloneName(v.ID)}
	}
	cp := v.CloneProgress
	req := cloneRequest(v)
	// Target-owned registration comes first: its own security groups and its own
	// network Attachment, never the source plan.
	if !v.SecurityGroupsInitialized {
		if len(v.Spec.SecurityGroupIDs) > 0 {
			if e := w.initializeSecurityGroups(ctx, v); e != nil {
				return e
			}
		} else {
			v.SecurityGroupsInitialized = true
			v.SecurityGroupIDs = []string{}
		}
	}
	if e := InitializeMedia(v); e != nil {
		return e
	}
	if v.Network.AttachmentID == "" {
		if e := w.checkpoint(ctx, o, *v, "prepare-network", ObjectRef{}); e != nil {
			return e
		}
		plan, e := w.network.Prepare(ctx, *v)
		if e != nil {
			return e
		}
		v.Network = plan
		o.Pending = nil
		return nil
	}
	if o.Step == "" && cp.UID == "" && (o.Pending == nil || o.Pending.Action != cloneStepSubmit) {
		eligibility, e := native.ObserveSourceCloneEligibility(ctx, *v)
		if e != nil {
			return e
		}
		if len(eligibility.Unsupported) > 0 {
			reason := eligibility.Error()
			if e = w.releaseCloneSource(ctx, sources, *v, "CANCELLED", "unsubmitted: "+reason.Error()); e != nil {
				return e
			}
			return fmt.Errorf("%w: %s", ErrCloneSourceUnavailable, reason)
		}
		if len(eligibility.NotReady) > 0 {
			// A recoverable native dependency keeps the accepted operation.
			return fmt.Errorf("%w: %s", ErrDependency, eligibility.Error())
		}
		if e = w.checkpoint(ctx, o, *v, cloneStepSubmit, ObjectRef{Kind: "VirtualMachineClone", Namespace: v.Spec.Namespace, Name: cp.Name}); e != nil {
			return e
		}
	}
	if cp.UID == "" && o.Pending != nil && o.Pending.Action == cloneStepSubmit {
		state, e := native.EnsureClone(ctx, *v, req)
		if e != nil {
			if errors.Is(e, ErrInvalid) {
				// The native API definitively rejected the submission: no object can
				// exist, so close this create and release the source.
				if x := w.releaseCloneSource(ctx, sources, *v, "CANCELLED", "clone submission rejected"); x != nil {
					return x
				}
				return fmt.Errorf("%w: %s", ErrCloneSourceUnavailable, e)
			}
			return e
		}
		cp.UID, cp.Phase = state.UID, state.Phase
		o.Pending = nil
		return w.repo.Save(ctx, *o, *v)
	}
	if o.Step == cloneStepDelete || o.Step == cloneStepFail {
		return w.finishClone(ctx, o, v, native, sources, req)
	}
	state, e := native.ObserveClone(ctx, *v, req)
	if e != nil {
		return e
	}
	if !state.Exists {
		if cp.RegistrationCompleted {
			// Registered delivery already persisted; continue the release path.
			o.Step = cloneStepDelete
			return w.finishClone(ctx, o, v, native, sources, req)
		}
		return fmt.Errorf("%w: clone disappeared before registration", ErrUnknown)
	}
	cp.UID, cp.Phase = state.UID, state.Phase
	if state.Failed {
		if state.Target.UID == "" && !CloneVolumesCovered(v.FrozenVolumeNames(), state.Outputs) {
			// No usable target and no complete named output: keep the producer and
			// the source HELD, and hand the residual case to explicit disposal.
			return fmt.Errorf("%w: clone failed without usable outputs: %s", ErrUnknown, state.Reason)
		}
		cp.PendingVolumes = mergeVolumeRefs(cp.PendingVolumes, state.Outputs)
		if state.Target.UID != "" {
			target := state.Target
			cp.PendingTargetRef = &target
		}
		o.Step = cloneStepFail
		o.Reason = state.Reason
		return w.repo.Checkpoint(ctx, *o, *v)
	}
	if !state.Succeeded || state.SnapshotName != "" || state.RestoreName != "" {
		if state.Reason != "" {
			o.Reason = state.Reason
		}
		return w.repo.Save(ctx, *o, *v)
	}
	// Succeeded and both temporary names are cleared: adapt the stopped target and
	// register the volumes it actually references in one Save.
	disks, target, e := native.ApplyCloneTargetConfiguration(ctx, *v, req)
	if e != nil {
		return e
	}
	v.Native = target
	registered := make([]Disk, 0, len(disks))
	for _, disk := range disks {
		registered = append(registered, disk)
	}
	v.Disks = registered
	cp.RegistrationCompleted = true
	cp.PendingTargetRef = &target
	cp.PendingVolumes = mergeVolumeRefs(cp.PendingVolumes, volumeRefsFromDisks(disks))
	v.Desired = "Stopped"
	v.Observed = "Stopped"
	v.Reason = ""
	v.ObservedAt = time.Now().UTC()
	o.Step = cloneStepDelete
	return w.repo.Save(ctx, *o, *v)
}

// finishClone deletes the adopted clone, waits for it to be gone, releases the
// source HELD and then finishes or starts the target. The five-step order is a
// hard contract: register -> checkpoint delete intent -> delete -> gone -> release.
func (w *Worker) finishClone(ctx context.Context, o *Operation, v *VM, native CloneNativePort, sources CloneSourcePort, req CloneRequest) error {
	cp := v.CloneProgress
	if !cp.CloneDeleteRequested {
		cp.CloneDeleteRequested = true
		return w.repo.Checkpoint(ctx, *o, *v)
	}
	gone, e := native.DeleteClone(ctx, *v, req)
	if e != nil {
		return e
	}
	if !gone {
		return nil
	}
	kind, evidence := "REGISTERED", "target "+v.Native.UID
	if v.Native.UID == "" {
		evidence = "clone gone; no registered target"
	}
	if e = w.releaseCloneSource(ctx, sources, *v, kind, evidence); e != nil {
		return e
	}
	cp.CloneDeleteRequested = false
	if o.Step == cloneStepFail {
		o.State = "failed"
		o.Step = ""
		v.Observed = "Failed"
		return nil
	}
	if v.Spec.StartAfterCreate {
		v.Desired = "Running"
		return w.power(ctx, o, v)
	}
	o.State = "succeeded"
	o.Step = ""
	return nil
}

func (w *Worker) releaseCloneSource(ctx context.Context, sources CloneSourcePort, v VM, kind, evidence string) error {
	reason := v.Reason
	if reason == "" {
		reason = evidence
	}
	return sources.ReleaseCloneSourceHELD(ctx, CloneSourceRelease{
		TenantID: v.TenantID, TargetVMID: v.ID, Kind: kind, By: "worker", Reason: reason, Evidence: evidence,
	})
}

// cancelClone takes over a user Delete that arrived before registration. It
// reports whether the cancel responsibility is fully finished; false means the
// same tick should stop and retry after the native producer terminates.
func (w *Worker) cancelClone(ctx context.Context, o *Operation, v *VM) (bool, error) {
	if v.CloneProgress == nil || v.CloneProgress.RegistrationCompleted {
		return true, nil
	}
	if v.CloneProgress.CancelHandled {
		// The source reference is already released; the ordinary delete continues.
		return true, nil
	}
	native, ok := w.native.(CloneNativePort)
	if !ok {
		return false, ErrDependency
	}
	sources, ok := w.repo.(CloneSourcePort)
	if !ok {
		return false, ErrDependency
	}
	cp := v.CloneProgress
	req := cloneRequest(v)
	if cp.UID == "" && !cp.CloneDeleteRequested {
		// No submission intent was ever checkpointed, so nothing could be in flight.
		if e := w.releaseCloneSource(ctx, sources, *v, "CANCELLED", "cancel before submit"); e != nil {
			return false, e
		}
		cp.CancelHandled = true
		return true, w.repo.Checkpoint(ctx, *o, *v)
	}
	state, e := native.ObserveClone(ctx, *v, req)
	if e != nil {
		return false, e
	}
	if !state.Exists {
		if len(cp.PendingVolumes) == 0 {
			// A submitted clone that vanished without a saved output keeps the HELD
			// reference; only the controlled CLI may release this case.
			return false, fmt.Errorf("%w: clone gone before registration without outputs", ErrUnknown)
		}
		gone, e := native.DeleteClone(ctx, *v, req)
		if e != nil || !gone {
			return false, e
		}
		if e = w.releaseCloneSource(ctx, sources, *v, "CANCELLED", "cancel after clone gone"); e != nil {
			return false, e
		}
		cp.CancelHandled = true
		return true, w.repo.Checkpoint(ctx, *o, *v)
	}
	early := state.Pending && state.Phase == "" && state.SnapshotName == "" && state.RestoreName == "" && state.Target.UID == "" && len(state.Outputs) == 0
	if !early && !CloneVolumesCovered(v.FrozenVolumeNames(), state.Outputs) && state.Target.UID == "" {
		// Still in a phase that may produce named outputs; keep the same clone and
		// wait instead of guessing zero volumes.
		cp.Phase = state.Phase
		return false, nil
	}
	cp.PendingVolumes = mergeVolumeRefs(cp.PendingVolumes, state.Outputs)
	if state.Target.UID != "" {
		target := state.Target
		cp.PendingTargetRef = &target
	}
	cp.CloneDeleteRequested = true
	if e = w.repo.Checkpoint(ctx, *o, *v); e != nil {
		return false, e
	}
	gone, e := native.DeleteClone(ctx, *v, req)
	if e != nil || !gone {
		return false, e
	}
	if e = w.releaseCloneSource(ctx, sources, *v, "CANCELLED", "user cancelled clone target"); e != nil {
		return false, e
	}
	cp.CancelHandled = true
	return true, w.repo.Checkpoint(ctx, *o, *v)
}

func mergeVolumeRefs(current, next []VolumeRef) []VolumeRef {
	merged := append([]VolumeRef(nil), current...)
	for _, candidate := range next {
		found := false
		for i := range merged {
			if merged[i].VolumeName == candidate.VolumeName {
				if candidate.PVCName != "" {
					merged[i].PVCName = candidate.PVCName
				}
				if candidate.PVCUID != "" {
					merged[i].PVCUID = candidate.PVCUID
				}
				found = true
				break
			}
		}
		if !found {
			merged = append(merged, candidate)
		}
	}
	return merged
}

func volumeRefsFromDisks(disks []Disk) []VolumeRef {
	refs := make([]VolumeRef, 0, len(disks))
	for _, d := range disks {
		refs = append(refs, VolumeRef{VolumeName: d.VolumeName, PVCName: d.PVC.Name, PVCUID: d.PVC.UID, DVName: d.DV.Name, DVUID: d.DV.UID})
	}
	return refs
}
