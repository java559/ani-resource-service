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
	cloneStepSubmit         = "clone-submit"
	cloneStepDelete         = "clone-delete"
	cloneStepFail           = "clone-failed-cleanup"
	cloneStepCleanupVM      = "clone-cleanup-vm"
	cloneStepCleanupVolumes = "clone-cleanup-volumes"
	cloneStepCleanupNetwork = "clone-cleanup-network"
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
		// Pre-submit only the cheap recheck runs: admission already read and froze
		// the source PVCs, StorageClass and Secret.
		eligibility, e := native.RecheckCloneSource(ctx, *v)
		if e != nil {
			return e
		}
		if len(eligibility.Unsupported) > 0 {
			cause := eligibility.UnsupportedError()
			done, e := w.releaseUnsubmitted(ctx, o, v, sources, "unsubmitted: "+cause.Error(), "no submit intent; network released")
			if e != nil {
				return e
			}
			if !done {
				// Keep the accepted operation claimable until the target-owned
				// network hand-off is really released.
				return ErrDependency
			}
			return cause
		}
		if len(eligibility.NotReady) > 0 {
			// A recoverable native dependency keeps the accepted operation.
			return eligibility.NotReadyError()
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
				// exist, so close this create, release the network hand-off and then
				// the source reference.
				cause := fmt.Errorf("%w: %s", ErrCloneSourceUnavailable, e)
				done, x := w.releaseUnsubmitted(ctx, o, v, sources, "unsubmitted: "+cause.Error(), "no submit intent; clone submission rejected")
				if x != nil {
					return x
				}
				if !done {
					return ErrDependency
				}
				return cause
			}
			return e
		}
		cp.UID, cp.Phase = state.UID, state.Phase
		o.Pending = nil
		if cp.UID == "" {
			return fmt.Errorf("%w: clone %s was created without an identity", ErrUnknown, req.Name)
		}
		return w.repo.Save(ctx, *o, *v)
	}
	if isCloneFinishStep(o.Step) {
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
	// A recorded UID is the only object this operation owns; a same-name object
	// with another UID is never adopted.
	if cp.UID != "" && cp.UID != state.UID {
		return ErrOwnership
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
// A failed clone additionally removes its own target artifacts before the
// operation becomes terminal failed; each cleanup step stays claimable.
func (w *Worker) finishClone(ctx context.Context, o *Operation, v *VM, native CloneNativePort, sources CloneSourcePort, req CloneRequest) error {
	cp := v.CloneProgress
	if o.Step == cloneStepDelete || o.Step == cloneStepFail {
		if !cp.CloneDeleteRequested {
			cp.CloneDeleteRequested = true
			return w.repo.Checkpoint(ctx, *o, *v)
		}
		result, e := native.DeleteClone(ctx, *v, req, cp.UID, "")
		if e != nil {
			return e
		}
		if result.Conflict {
			// The clone changed between observation and delete. The next tick
			// re-reads the same object; the recorded UID is still the only owner.
			return nil
		}
		if !result.Gone {
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
			return w.cleanupFailedClone(ctx, o, v, native)
		}
		if v.Spec.StartAfterCreate {
			o.Step = ""
			v.Desired = "Running"
			return w.power(ctx, o, v)
		}
		o.State = "succeeded"
		o.Step = ""
		return nil
	}
	return w.cleanupFailedClone(ctx, o, v, native)
}

// isCloneFinishStep reports whether this step belongs to the shared finish path.
func isCloneFinishStep(step string) bool {
	switch step {
	case cloneStepDelete, cloneStepFail, cloneStepCleanupVM, cloneStepCleanupVolumes, cloneStepCleanupNetwork:
		return true
	}
	return false
}

// cleanupFailedClone removes the target-owned artifacts of a failed clone. It
// advances one durable step per tick so a crash never hides a residual target.
func (w *Worker) cleanupFailedClone(ctx context.Context, o *Operation, v *VM, native CloneNativePort) error {
	cp := v.CloneProgress
	target := v.Native
	if target.UID == "" && cp.PendingTargetRef != nil {
		target = *cp.PendingTargetRef
	}
	if target.UID == "" && len(cp.PendingVolumes) == 0 {
		return w.finishFailedClone(o, v)
	}
	if o.Step == cloneStepFail {
		// Release is complete; enter the target cleanup sequence.
		o.Step = cloneStepCleanupVM
		return w.repo.Checkpoint(ctx, *o, *v)
	}
	if o.Step == cloneStepCleanupVM {
		if target.UID == "" {
			o.Step = cloneStepCleanupVolumes
			return w.repo.Checkpoint(ctx, *o, *v)
		}
		gone, e := native.DeleteCloneTarget(ctx, *v, target)
		if e != nil {
			return e
		}
		if !gone {
			return nil
		}
		o.Step = cloneStepCleanupVolumes
		return w.repo.Checkpoint(ctx, *o, *v)
	}
	if o.Step == cloneStepCleanupVolumes {
		cleanup, ok := w.native.(CloneCleanupPort)
		if !ok {
			if len(cp.PendingVolumes) > 0 {
				return ErrDependency
			}
		} else {
			done, e := cleanup.DeleteNamedVolumes(ctx, *v, cp.PendingVolumes)
			if e != nil {
				return e
			}
			if !done {
				return nil
			}
		}
		o.Step = cloneStepCleanupNetwork
		return w.repo.Checkpoint(ctx, *o, *v)
	}
	if o.Step == cloneStepCleanupNetwork {
		if e := w.checkpoint(ctx, o, *v, "release-network", ObjectRef{}); e != nil {
			return e
		}
		done, e := w.network.Release(ctx, *v)
		if e != nil {
			return e
		}
		if !done {
			return nil
		}
		if w.securityGroups != nil {
			if e := w.securityGroups.ReleaseSecurityGroupBinding(ctx, v.TenantID, "vm", v.ID, v.Version); e != nil {
				return e
			}
		} else if len(v.SecurityGroupIDs) > 0 || len(v.Spec.SecurityGroupIDs) > 0 {
			return ErrDependency
		}
		return w.finishFailedClone(o, v)
	}
	return w.finishFailedClone(o, v)
}

// finishFailedClone closes the failed operation only after every owned artifact
// is gone.
func (w *Worker) finishFailedClone(o *Operation, v *VM) error {
	o.State = "failed"
	o.Step = ""
	o.Pending = nil
	v.Observed = "Failed"
	return nil
}

// releaseUnsubmitted reclaims the target-owned network hand-off and the source
// HELD of a create that provably never submitted a clone. done=false means a
// release step is still pending, so the caller keeps the operation claimable.
func (w *Worker) releaseUnsubmitted(ctx context.Context, o *Operation, v *VM, sources CloneSourcePort, reason, evidence string) (bool, error) {
	if v.Network.AttachmentID != "" || v.Network.SubmissionID != "" {
		if e := w.checkpoint(ctx, o, *v, "release-network", ObjectRef{}); e != nil {
			return false, e
		}
		done, e := w.network.Release(ctx, *v)
		if e != nil {
			return false, e
		}
		if !done {
			return false, nil
		}
	}
	if w.securityGroups != nil {
		if e := w.securityGroups.ReleaseSecurityGroupBinding(ctx, v.TenantID, "vm", v.ID, v.Version); e != nil {
			return false, e
		}
	} else if len(v.SecurityGroupIDs) > 0 || len(v.Spec.SecurityGroupIDs) > 0 {
		return false, ErrDependency
	}
	if e := w.releaseCloneSourceReason(ctx, sources, *v, "CANCELLED", reason, evidence); e != nil {
		return false, e
	}
	o.Pending = nil
	return true, nil
}

func (w *Worker) releaseCloneSource(ctx context.Context, sources CloneSourcePort, v VM, kind, evidence string) error {
	reason := v.Reason
	if reason == "" {
		reason = evidence
	}
	return w.releaseCloneSourceReason(ctx, sources, v, kind, reason, evidence)
}

func (w *Worker) releaseCloneSourceReason(ctx context.Context, sources CloneSourcePort, v VM, kind, reason, evidence string) error {
	return sources.ReleaseCloneSourceHELD(ctx, CloneSourceRelease{
		TenantID: v.TenantID, TargetVMID: v.ID, Kind: kind, By: "worker", Reason: reason, Evidence: evidence,
	})
}

// cancelClone takes over a user Delete of a clone that still owns a source
// reference. It reports whether the cancel responsibility is fully finished;
// false means the same tick should stop and retry after the native producer
// terminates.
func (w *Worker) cancelClone(ctx context.Context, o *Operation, v *VM) (bool, error) {
	if v.CloneProgress == nil {
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
	if cp.RegistrationCompleted {
		// A delete may arrive before the create operation finishes its release.
		// Finishing it here keeps a HELD row from outliving a registered target;
		// both the clone delete and the release are idempotent.
		result, e := native.DeleteClone(ctx, *v, cloneRequest(v), cp.UID, "")
		if e != nil {
			return false, e
		}
		if result.Conflict || !result.Gone {
			return false, nil
		}
		evidence := "target " + v.Native.UID
		if v.Native.UID == "" {
			evidence = "registered target absent"
		}
		if e = w.releaseCloneSource(ctx, sources, *v, "REGISTERED", evidence); e != nil && !errors.Is(e, ErrNotFound) {
			return false, e
		}
		cp.CancelHandled = true
		return true, w.repo.Checkpoint(ctx, *o, *v)
	}
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
	return w.cancelObservedClone(ctx, o, v, native, sources, state, false)
}

// cancelObservedClone evaluates one clone observation. A delete conflict is
// never resolved with the stale object: the fresh observation is re-evaluated
// once, and a second conflict waits for the next tick.
func (w *Worker) cancelObservedClone(ctx context.Context, o *Operation, v *VM, native CloneNativePort, sources CloneSourcePort, state CloneObservation, retried bool) (bool, error) {
	cp := v.CloneProgress
	req := cloneRequest(v)
	if !state.Exists {
		if len(cp.PendingVolumes) == 0 {
			// A submitted clone that vanished without a saved output keeps the HELD
			// reference; only the controlled CLI may release this case.
			return false, fmt.Errorf("%w: clone gone before registration without outputs", ErrUnknown)
		}
		result, e := native.DeleteClone(ctx, *v, req, state.UID, state.ResourceVersion)
		if e != nil {
			return false, e
		}
		if result.Conflict {
			if retried {
				return false, nil
			}
			return w.cancelObservedClone(ctx, o, v, native, sources, result.Current, true)
		}
		if !result.Gone {
			return false, nil
		}
		return w.finishCancelledClone(ctx, o, v, sources, "CANCELLED", "cancel after clone gone")
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
	if e := w.repo.Checkpoint(ctx, *o, *v); e != nil {
		return false, e
	}
	result, e := native.DeleteClone(ctx, *v, req, state.UID, state.ResourceVersion)
	if e != nil {
		return false, e
	}
	if result.Conflict {
		if retried {
			return false, nil
		}
		return w.cancelObservedClone(ctx, o, v, native, sources, result.Current, true)
	}
	if !result.Gone {
		return false, nil
	}
	return w.finishCancelledClone(ctx, o, v, sources, "CANCELLED", "user cancelled clone target")
}

// finishCancelledClone removes the named outputs a cancelled clone already
// produced, then releases the source reference. A clone that never registered
// wrote no vm.disks row, so the ordinary delete path cannot see these PVCs; the
// recorded PendingVolumes are the only inventory of what the cancelled clone
// created. The list is already checkpointed by the caller that recorded it, so
// this step is retried idempotently: the release only happens once every owned
// volume is gone.
func (w *Worker) finishCancelledClone(ctx context.Context, o *Operation, v *VM, sources CloneSourcePort, kind, evidence string) (bool, error) {
	cp := v.CloneProgress
	if len(cp.PendingVolumes) > 0 {
		cleanup, ok := w.native.(CloneCleanupPort)
		if !ok {
			return false, ErrDependency
		}
		done, e := cleanup.DeleteNamedVolumes(ctx, *v, cp.PendingVolumes)
		if e != nil {
			return false, e
		}
		if !done {
			return false, nil
		}
	}
	if e := w.releaseCloneSource(ctx, sources, *v, kind, evidence); e != nil {
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
