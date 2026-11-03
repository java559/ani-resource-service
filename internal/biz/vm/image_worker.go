package vm

import (
	"context"
	"errors"
	"log/slog"
	"time"
)

// ImageRepository and ImageNativePort are already declared in image.go; this
// file owns only the background driver so the command path stays testable.

// ImageWorker advances one image record per claim. It never holds a database
// transaction across a cluster call, and every step is re-entrant.
type ImageWorker struct {
	repo        ImageRepository
	native      ImageNativePort
	environment EnvironmentPort
	identity    string
	cluster     string
	lease       time.Duration
	poll        time.Duration
	logger      *slog.Logger
	// enabled gates only new admissions; existing records always drain.
	enabled func() bool
}

func NewImageWorker(repo ImageRepository, native ImageNativePort, environment EnvironmentPort, identity string, enabled func() bool, logger *slog.Logger) (*ImageWorker, error) {
	if repo == nil || native == nil || environment == nil || identity == "" || !UUID(identity) || enabled == nil || logger == nil {
		return nil, ErrInvalid
	}
	return &ImageWorker{repo: repo, native: native, environment: environment, identity: identity,
		lease: 45 * time.Second, poll: 2 * time.Second, logger: logger, enabled: enabled}, nil
}

// Step implements the shared background Stepper used by every resource lane.
func (w *ImageWorker) Step(ctx context.Context) (bool, error) {
	i, claimed, e := w.repo.ClaimImage(ctx, w.identity, w.lease)
	if e != nil {
		if errors.Is(e, ErrNotFound) {
			return false, nil
		}
		return false, e
	}
	if !claimed {
		return false, nil
	}
	e = w.advance(ctx, i)
	if e == nil || errors.Is(e, ErrImageVersion) || errors.Is(e, ErrLease) {
		return true, nil
	}
	// A dependency failure keeps the lease holder's classification but must not
	// stop the lane; back off this record and report the reason code only.
	i.NextAttemptAt = time.Now().Add(imageBackoff(i))
	i.Reason = imageFailureReason(e)
	if save := w.repo.SaveImage(ctx, i); save != nil {
		return true, save
	}
	w.logger.Warn("VM image background step failed", "image_id", i.ID, "state", i.State, "reason", i.Reason)
	return true, nil
}

// imageBackoff is 2s while work is in flight and widens once a record has
// repeatedly failed, so a stuck dependency cannot spin the lane.
func imageBackoff(i Image) time.Duration {
	if i.State == StateDeleting || i.CleanupState == CleanupPending || i.CleanupState == CleanupRunning {
		return 5 * time.Second
	}
	return 2 * time.Second
}

func imageFailureReason(e error) string {
	switch {
	case errors.Is(e, ErrImageDependency), errors.Is(e, ErrDependency):
		return "VM_IMAGE_DEPENDENCY_UNAVAILABLE"
	case errors.Is(e, ErrImageIdentity), errors.Is(e, ErrChanged):
		return "SOURCE_IDENTITY_CHANGED"
	default:
		return "SOURCE_VALIDATION_FAILED"
	}
}

func (w *ImageWorker) advance(ctx context.Context, i Image) error {
	// Cleanup wins over any upload progress: a delete or deadline that already
	// closed the record must not be reopened by a late success.
	if i.CleanupState == CleanupPending || i.CleanupState == CleanupRunning || i.State == StateDeleting {
		return w.cleanup(ctx, i)
	}
	switch i.State {
	case StatePreparing:
		return w.prepare(ctx, i)
	case StateImporting:
		return w.importing(ctx, i)
	case StateVerifying:
		return w.verifying(ctx, i)
	case StateReady:
		return w.revalidate(ctx, i)
	}
	return nil
}

// prepare binds the tenant namespace and the source DataVolume. The namespace
// identity is frozen here because the media projection never re-resolves it.
func (w *ImageWorker) prepare(ctx context.Context, i Image) error {
	if !w.enabled() {
		// The switch stops new admissions, so a record that has not created its
		// native target yet is failed rather than left to accumulate.
		i.Reason = "VM_IMAGE_UPLOAD_DISABLED"
		i.State = StateFailed
		i.CleanupState = CleanupNone
		i.UploadDeadline = nil
		return w.repo.SaveImage(ctx, i)
	}
	if i.Native.Namespace == "" {
		ns, e := w.environment.ResolveNamespace(ctx, i.TenantID, "")
		if e != nil {
			if errors.Is(e, ErrDependency) || errors.Is(e, ErrNotFound) {
				i.Reason = "NAMESPACE_INVALID"
				return w.repo.SaveImage(ctx, i)
			}
			return e
		}
		i.Native.Namespace, i.Native.NamespaceUID = ns.Name, ns.UID
	}
	i.Native.ClusterID = w.clusterID()
	observed, e := w.native.EnsureImageSource(ctx, i)
	if e != nil {
		return e
	}
	if observed.Native.DVUID == "" {
		return ErrImageDependency
	}
	observed.State = StateImporting
	observed.Reason = ""
	return w.repo.SaveImage(ctx, observed)
}

func (w *ImageWorker) importing(ctx context.Context, i Image) error {
	observed, e := w.native.ObserveImageSource(ctx, i)
	if errors.Is(e, ErrDependency) {
		observed = i
	} else if e != nil {
		if errors.Is(e, ErrOwnership) || errors.Is(e, ErrImageIdentity) {
			i.Reason = "SOURCE_IDENTITY_CHANGED"
			i.State = StateFailed
			i.CleanupState = CleanupPending
			i.UploadDeadline = nil
			return w.repo.SaveImage(ctx, i)
		}
		return e
	}
	if observed.Reason == "UPLOAD_REJECTED" || observed.Reason == "CAPACITY_INSUFFICIENT" {
		observed.State = StateFailed
		observed.CleanupState = CleanupPending
		observed.UploadDeadline = nil
		return w.repo.SaveImage(ctx, observed)
	}
	if !observed.ReadyForVerification() {
		observed.NextAttemptAt = time.Now().Add(w.poll)
		return w.repo.SaveImage(ctx, observed)
	}
	verified, e := w.native.EnsureImageVerification(ctx, observed)
	if e != nil {
		return e
	}
	verified.State = StateVerifying
	verified.Reason = ""
	return w.repo.SaveImage(ctx, verified)
}

// revalidate keeps READY honest. A source whose identity moved can never back a
// new VM, but already admitted consumers keep their own copies untouched.
func (w *ImageWorker) revalidate(ctx context.Context, i Image) error {
	observed, e := w.native.ObserveImageSourceIdentity(ctx, i)
	observed.NextAttemptAt = time.Now().Add(imageRevalidateInterval)
	if e == nil {
		return w.repo.SaveImage(ctx, observed)
	}
	if errors.Is(e, ErrDependency) || errors.Is(e, ErrNotFound) ||
		errors.Is(e, ErrOwnership) || errors.Is(e, ErrImageIdentity) || errors.Is(e, ErrChanged) {
		i.State = StateUnavail
		i.Reason = "SOURCE_IDENTITY_CHANGED"
		i.Native.DVPhase = ""
		i.NextAttemptAt = time.Now().Add(imageRevalidateInterval)
		return w.repo.SaveImage(ctx, i)
	}
	return e
}

var imageRevalidateInterval = time.Hour

func (w *ImageWorker) verifying(ctx context.Context, i Image) error {
	observed, result, e := w.native.ObserveImageVerification(ctx, i)
	if errors.Is(e, ErrDependency) {
		observed.NextAttemptAt = time.Now().Add(w.poll)
		return w.repo.SaveImage(ctx, observed)
	}
	if e != nil {
		if errors.Is(e, ErrImageIdentity) || errors.Is(e, ErrOwnership) {
			observed.Reason = "SOURCE_IDENTITY_CHANGED"
			observed.State = StateFailed
			observed.CleanupState = CleanupPending
			observed.UploadDeadline = nil
			return w.repo.SaveImage(ctx, observed)
		}
		return e
	}
	if !result.Pass {
		observed.Reason = result.FailureReason
		if observed.Reason == "" {
			observed.Reason = "SOURCE_VALIDATION_FAILED"
		}
		observed.State = StateFailed
		observed.CleanupState = CleanupPending
		observed.UploadDeadline = nil
		return w.repo.SaveImage(ctx, observed)
	}
	if err := w.native.PublishSourceAnnotation(ctx, observed); err != nil {
		if errors.Is(err, ErrImageVersion) {
			// Someone else changed the annotation; re-observe before deciding.
			observed.NextAttemptAt = time.Now().Add(w.poll)
			return w.repo.SaveImage(ctx, observed)
		}
		return err
	}
	// The annotation is durable before READY, so ValidateSource can never see a
	// READY record whose source identity is unwritten.
	if err := observed.PublishReady(result.PVCUID, result.RequestedBytes, result.ObservedBytes, result.Bytes, result.Checksum, time.Now().UTC()); err != nil {
		observed.Reason = "SOURCE_VALIDATION_FAILED"
		observed.State = StateFailed
		observed.CleanupState = CleanupPending
		observed.UploadDeadline = nil
		return w.repo.SaveImage(ctx, observed)
	}
	observed.NextAttemptAt = time.Now().Add(imageRevalidateInterval)
	return w.repo.SaveImage(ctx, observed)
}

// cleanup removes exactly the registered chain, then closes the tombstone.
func (w *ImageWorker) cleanup(ctx context.Context, i Image) error {
	if i.CleanupState == CleanupComplete {
		return w.closeDeleted(ctx, i)
	}
	if i.Native.DVUID == "" && i.Native.PVCUID == "" && i.Native.JobUID == "" && i.Native.TokenUID == "" && len(i.Cleanup) == 0 && i.Native.Namespace == "" {
		i.CleanupState = CleanupComplete
		i.NextAttemptAt = time.Now().Add(w.poll)
		return w.repo.SaveImage(ctx, i)
	}
	i.CleanupState = CleanupRunning
	observed, done, e := w.native.DeleteImageObjects(ctx, i)
	if e != nil {
		if errors.Is(e, ErrImageCleanup) || errors.Is(e, ErrOwnership) {
			observed.CleanupState = CleanupBlocked
			observed.Reason = "NATIVE_CLEANUP_BLOCKED"
			if save := w.repo.SaveImage(ctx, observed); save != nil && !errors.Is(save, ErrImageVersion) {
				return save
			}
			w.logger.Warn("VM image native cleanup blocked", "image_id", i.ID, "reason", observed.Reason)
			return nil
		}
		return e
	}
	if !done {
		observed.NextAttemptAt = time.Now().Add(imageBackoff(observed))
		return w.repo.SaveImage(ctx, observed)
	}
	observed.CleanupState = CleanupComplete
	return w.closeDeleted(ctx, observed)
}

func (w *ImageWorker) closeDeleted(ctx context.Context, i Image) error {
	if i.State != StateDeleting {
		i.CleanupState = CleanupComplete
		return w.repo.SaveImage(ctx, i)
	}
	i.State = StateDeleted
	i.Reason = ""
	i.CleanupState = CleanupComplete
	i.Cleanup = nil
	return w.repo.SaveImage(ctx, i)
}

// SetImageClusterID lets the composition root hand the worker the same cluster
// identity the VM lane already freezes, without a second configuration source.
func (w *ImageWorker) SetImageClusterID(cluster string) {
	if cluster != "" {
		w.cluster = cluster
	}
}

func (w *ImageWorker) clusterID() string { return w.cluster }

var _ = slog.Default
