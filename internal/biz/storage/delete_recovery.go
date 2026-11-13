package storage

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/telemetry"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/telemetry/logfields"
)

const deleteLeaseTTL = 45 * time.Second
const deleteRetryInterval = 5 * time.Second

// deleteRecoveryOperation names every structured log record this file emits,
// shared by the HTTP delete path and the recovery worker pass.
const deleteRecoveryOperation = "storage.delete_recovery"

// logDeleteStep writes one structured failure record for a delete-guard step.
// Empty identity fields are skipped; err is handed to logfields so the native
// Kubernetes/PostgreSQL cause keeps its own redacted, length-capped record
// with error_source/error_code next to the stable business code. Extra attrs
// follow the identity block.
func logDeleteStep(logger *slog.Logger, level slog.Level, ctx context.Context, operation, msg string, id PVCIdentity, err error, extra ...any) {
	if logger == nil {
		return
	}
	fields := make([]any, 0, 12+len(extra))
	fields = append(fields, slog.String("operation", operation))
	if id.TenantID != "" {
		fields = append(fields, slog.String("tenant_id", id.TenantID))
	}
	if id.ClusterID != "" {
		fields = append(fields, slog.String("cluster_id", id.ClusterID))
	}
	if id.Namespace != "" {
		fields = append(fields, slog.String("namespace", id.Namespace))
	}
	if id.PVCName != "" {
		fields = append(fields, slog.String("pvc_name", id.PVCName))
	}
	if id.TargetUID != "" {
		fields = append(fields, slog.String("target_uid", id.TargetUID))
	}
	if err != nil {
		for _, attr := range logfields.ErrorAttrs(err) {
			fields = append(fields, attr)
		}
	}
	fields = append(fields, extra...)
	logger.Log(ctx, level, msg, fields...)
}

// causeText renders a prior failure as log-only text; it never joins a match chain.
func causeText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func (u *UseCase) deleteGuardReady() bool {
	return u.cfg.BorrowedPVCGuard != nil && CanonicalUUID(u.cfg.ClusterID) && u.cfg.DeleteExecutor != ""
}

// guardUnavailableReason names which precondition failed, for the log record.
func (u *UseCase) guardUnavailableReason() string {
	switch {
	case u.cfg.BorrowedPVCGuard == nil:
		return "guard_not_configured"
	case !CanonicalUUID(u.cfg.ClusterID):
		return "cluster_id_invalid"
	default:
		return "executor_missing"
	}
}

// guardWriteLevel demotes a lost CAS lease — the routine concurrent-writer
// outcome — to DEBUG; other guard write failures stay ERROR.
func guardWriteLevel(err error) slog.Level {
	if errors.Is(err, ErrDeleteLease) {
		return slog.LevelDebug
	}
	return slog.LevelError
}

func (u *UseCase) deleteGet(ctx context.Context, namespace, name string) (*VolumeRecord, error) {
	call, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return u.volumes.GetVolume(call, namespace, name)
}

func (u *UseCase) deleteNamespace(ctx context.Context, tenant string) (string, error) {
	call, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return u.ensureNamespace(call, tenant)
}

func (u *UseCase) deleteVolumeGuarded(ctx context.Context, tenant, name, expectedUID string) (DeleteResult, error) {
	if !CanonicalUUID(tenant) || name == "" || len(name) > 253 || !dnsSubdomain.MatchString(name) || expectedUID == "" {
		return DeleteResult{}, ErrInvalid
	}
	// No guard means no fallback to a direct Kubernetes deletion.
	if !u.deleteGuardReady() {
		logDeleteStep(u.logger, slog.LevelError, ctx, deleteRecoveryOperation, "storage PVC delete guard unavailable",
			PVCIdentity{TenantID: tenant, ClusterID: u.cfg.ClusterID, PVCName: name}, nil, slog.String("reason", u.guardUnavailableReason()))
		return DeleteResult{}, ErrBackendUnavailable
	}
	step, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	namespace, err := u.deleteNamespace(step, tenant)
	if err != nil {
		logDeleteStep(u.logger, slog.LevelError, step, deleteRecoveryOperation, "storage PVC delete namespace resolve failed",
			PVCIdentity{TenantID: tenant, ClusterID: u.cfg.ClusterID, PVCName: name, TargetUID: expectedUID}, err)
		return DeleteResult{}, err
	}
	id := PVCIdentity{TenantID: tenant, ClusterID: u.cfg.ClusterID, Namespace: namespace, PVCName: name, TargetUID: expectedUID}
	current, getErr := u.deleteGet(step, namespace, name)
	if getErr != nil && !errors.Is(getErr, ErrNotFound) {
		logDeleteStep(u.logger, slog.LevelError, step, deleteRecoveryOperation, "storage PVC delete initial read failed", id, getErr)
		return DeleteResult{}, getErr
	}
	if getErr == nil {
		if !owned(current, tenant) {
			return DeleteResult{}, ErrNotFound
		}
		if current.UID != expectedUID {
			// An existing old-UID ticket remains protected and is completed by
			// recovery; the public call never adopts the same-name new object.
			return DeleteResult{}, ErrIdentityChanged
		}
	}
	record, mayAdvance, err := u.cfg.BorrowedPVCGuard.ReserveDelete(step, id, u.cfg.DeleteExecutor, deleteLeaseTTL)
	if err != nil {
		// Log the pre-classification cause so logfields sees the raw failure.
		logDeleteStep(u.logger, guardWriteLevel(err), step, deleteRecoveryOperation, "storage PVC delete reserve failed", id, err)
		return DeleteResult{}, deleteGuardError(err)
	}
	if record.Identity != id {
		return DeleteResult{}, ErrIdentityChanged
	}
	if mayAdvance {
		return u.advanceDelete(step, record)
	}
	// A retry cannot steal a valid lease. Only already known results may be
	// returned without advancing; otherwise it waits for that writer/recovery.
	if errors.Is(getErr, ErrNotFound) {
		return DeleteResult{State: DeletionGone}, nil
	}
	if record.Phase == "completed" {
		return DeleteResult{}, ErrBackendUnavailable
	}
	if current.Deleting {
		return DeleteResult{State: DeletionTerminating, Volume: toVolume(current)}, nil
	}
	if record.WriteStarted && record.LastWriteOutcome == "submitted" {
		return DeleteResult{State: DeletionSubmitted, Volume: toVolume(current)}, nil
	}
	return DeleteResult{}, ErrBackendUnavailable
}

func deleteGuardError(err error) error {
	if errors.Is(err, ErrVolumeInUse) || errors.Is(err, ErrIdentityChanged) || errors.Is(err, ErrNotFound) {
		return err
	}
	// Sentinel first, raw cause second: the guard's native failure (e.g.
	// PostgreSQL SQLSTATE) stays extractable while the public code is stable.
	return fmt.Errorf("%w: %w", ErrBackendUnavailable, err)
}

// invalidDeleteRecordReason returns the first failed invariant of a claimed
// record, or "" when the record may advance.
func (u *UseCase) invalidDeleteRecordReason(record DeleteRecord) string {
	id, token := record.Identity, record.Token
	namespace, err := u.namespace(id.TenantID)
	if err != nil || id.Namespace != namespace {
		return "identity_namespace_mismatch"
	}
	if id.ClusterID != u.cfg.ClusterID {
		return "cluster_mismatch"
	}
	if id.PVCName == "" {
		return "pvc_name_missing"
	}
	if id.TargetUID == "" {
		return "target_uid_missing"
	}
	if token.TicketID == "" {
		return "ticket_missing"
	}
	if token.GuardEpoch <= 0 {
		return "guard_epoch_invalid"
	}
	if token.LeaseOwner != u.cfg.DeleteExecutor {
		return "lease_owner_mismatch"
	}
	if token.LeaseEpoch <= 0 {
		return "lease_epoch_invalid"
	}
	if !token.LeaseUntil.After(u.now()) {
		return "lease_expired"
	}
	if record.Phase != "reserved" && record.Phase != "deleting" && record.Phase != "observing" {
		return "phase_invalid"
	}
	return ""
}

func (u *UseCase) recordDeleteWait(ctx context.Context, record DeleteRecord, observation DeleteObservation, cause error) (DeleteResult, error) {
	observation.RetryAt = u.now().Add(deleteRetryInterval)
	if err := u.cfg.BorrowedPVCGuard.RecordDeleteObservation(ctx, record.Identity, record.Token, observation); err != nil {
		logDeleteStep(u.logger, guardWriteLevel(err), ctx, deleteRecoveryOperation, "storage PVC delete observation write failed",
			record.Identity, err, slog.String("prior_cause", causeText(cause)))
		return DeleteResult{}, fmt.Errorf("%w: %v", ErrBackendUnavailable, err)
	}
	if cause != nil {
		return DeleteResult{}, cause
	}
	return DeleteResult{}, ErrBackendUnavailable
}

// advanceDelete is the only finite advancement path, shared by HTTP and
// recovery. It commits the possible-write marker before the UID-bound DELETE.
func (u *UseCase) advanceDelete(ctx context.Context, record DeleteRecord) (DeleteResult, error) {
	if reason := u.invalidDeleteRecordReason(record); reason != "" {
		level := slog.LevelError
		if reason == "lease_expired" {
			// A concurrent writer or recovery pass routinely expires this
			// claim's lease; that is the expected lost race, not a fault.
			level = slog.LevelDebug
		}
		logDeleteStep(u.logger, level, ctx, deleteRecoveryOperation, "storage PVC delete record failed invariants",
			record.Identity, nil, slog.String("reason", reason))
		return DeleteResult{}, ErrBackendUnavailable
	}
	id := record.Identity
	namespace, nsErr := u.deleteNamespace(ctx, id.TenantID)
	if nsErr != nil || namespace != id.Namespace {
		logDeleteStep(u.logger, slog.LevelError, ctx, deleteRecoveryOperation, "storage PVC delete namespace unavailable", id, nsErr,
			slog.String("observed_namespace", namespace), slog.String("prior_cause", causeText(nsErr)))
		return u.recordDeleteWait(ctx, record, DeleteObservation{State: "unavailable", WriteOutcome: record.LastWriteOutcome, Reason: "NAMESPACE_UNAVAILABLE"}, ErrBackendUnavailable)
	}
	current, err := u.deleteGet(ctx, id.Namespace, id.PVCName)
	if errors.Is(err, ErrNotFound) {
		return u.completeDelete(ctx, record, DeleteObservation{State: "absent", WriteOutcome: record.LastWriteOutcome})
	}
	if err != nil {
		logDeleteStep(u.logger, slog.LevelWarn, ctx, deleteRecoveryOperation, "storage PVC delete observation unavailable",
			id, err, slog.String("stage", "before_delete"))
		return u.recordDeleteWait(ctx, record, DeleteObservation{State: "unavailable", WriteOutcome: record.LastWriteOutcome, Reason: "PVC_OBSERVATION_UNAVAILABLE"}, ErrBackendUnavailable)
	}
	if current.UID != id.TargetUID {
		return u.completeDelete(ctx, record, DeleteObservation{State: "identity_changed", ObservedUID: current.UID, WriteOutcome: record.LastWriteOutcome})
	}
	if !owned(current, id.TenantID) {
		if !record.WriteStarted {
			if err := u.cfg.BorrowedPVCGuard.CancelUnsentDelete(ctx, id, record.Token, "PVC_OWNERSHIP_CHANGED"); err != nil {
				logDeleteStep(u.logger, guardWriteLevel(err), ctx, deleteRecoveryOperation, "storage PVC delete cancel-unsent write failed",
					id, err, slog.String("cancel_reason", "PVC_OWNERSHIP_CHANGED"))
				return DeleteResult{}, fmt.Errorf("%w: %v", ErrBackendUnavailable, err)
			}
			return DeleteResult{}, ErrNotFound
		}
		return u.recordDeleteWait(ctx, record, DeleteObservation{State: "present", ObservedUID: current.UID, WriteOutcome: record.LastWriteOutcome, Reason: "PVC_OWNERSHIP_CHANGED"}, ErrBackendUnavailable)
	}
	if !record.WriteStarted {
		// Reserved recovery must repeat the complete external usage check.
		// Once a write may have been sent, usage cannot cancel its barrier.
		consumers, usageErr := u.deleteUsage(ctx, id.Namespace, id.PVCName)
		if usageErr != nil {
			logDeleteStep(u.logger, slog.LevelWarn, ctx, deleteRecoveryOperation, "storage PVC delete usage unknown", id, usageErr)
			return u.recordDeleteWait(ctx, record, DeleteObservation{State: "present", ObservedUID: current.UID, WriteOutcome: "not_sent", Reason: "VOLUME_USAGE_UNKNOWN"}, usageErr)
		}
		if len(consumers) > 0 {
			if err := u.cfg.BorrowedPVCGuard.CancelUnsentDelete(ctx, id, record.Token, "VOLUME_IN_USE"); err != nil {
				logDeleteStep(u.logger, guardWriteLevel(err), ctx, deleteRecoveryOperation, "storage PVC delete cancel-unsent write failed",
					id, err, slog.String("cancel_reason", "VOLUME_IN_USE"))
				return DeleteResult{}, fmt.Errorf("%w: %v", ErrBackendUnavailable, err)
			}
			return DeleteResult{}, &InUseError{Consumers: consumers}
		}
	}
	if err := u.cfg.BorrowedPVCGuard.MarkDeleteWriteStarted(ctx, id, record.Token); err != nil {
		logDeleteStep(u.logger, guardWriteLevel(err), ctx, deleteRecoveryOperation, "storage PVC delete write-start mark failed", id, err)
		return DeleteResult{}, fmt.Errorf("%w: %v", ErrBackendUnavailable, err)
	}
	call, cancel := context.WithTimeout(ctx, 15*time.Second)
	writeErr := u.volumes.DeleteVolume(call, id.Namespace, id.PVCName, id.TargetUID)
	cancel()
	outcome, reason := "submitted", ""
	if writeErr != nil && !errors.Is(writeErr, ErrNotFound) {
		outcome, reason = "unknown", "PVC_DELETE_RESULT_UNKNOWN"
		if errors.Is(writeErr, ErrAccessDenied) || errors.Is(writeErr, ErrConflict) || errors.Is(writeErr, ErrIdentityChanged) {
			outcome, reason = "rejected", "PVC_DELETE_REJECTED"
		}
	}
	// Record the write verdict before the observation GET can supersede it;
	// a later absent reading never erases this earlier write-error log.
	if outcome == "rejected" {
		logDeleteStep(u.logger, slog.LevelError, ctx, deleteRecoveryOperation, "storage PVC delete rejected",
			id, writeErr, slog.String("outcome", outcome), slog.String("reason", reason))
	} else if outcome == "unknown" {
		logDeleteStep(u.logger, slog.LevelWarn, ctx, deleteRecoveryOperation, "storage PVC delete result unknown",
			id, writeErr, slog.String("outcome", outcome), slog.String("reason", reason))
	}
	if u.cfg.ObserveDeleteWrite != nil {
		u.cfg.ObserveDeleteWrite(outcome)
	}
	current, err = u.deleteGet(ctx, id.Namespace, id.PVCName)
	if errors.Is(err, ErrNotFound) {
		return u.completeDelete(ctx, record, DeleteObservation{State: "absent", WriteOutcome: outcome})
	}
	if err != nil {
		logDeleteStep(u.logger, slog.LevelWarn, ctx, deleteRecoveryOperation, "storage PVC delete observation unavailable",
			id, err, slog.String("stage", "after_delete"))
		return u.recordDeleteWait(ctx, record, DeleteObservation{State: "unavailable", WriteOutcome: outcome, Reason: "PVC_OBSERVATION_UNAVAILABLE"}, ErrBackendUnavailable)
	}
	if current.UID != id.TargetUID {
		return u.completeDelete(ctx, record, DeleteObservation{State: "identity_changed", ObservedUID: current.UID, WriteOutcome: outcome})
	}
	state, publicState := "present", DeletionSubmitted
	if current.Deleting {
		state, publicState = "terminating", DeletionTerminating
	}
	observation := DeleteObservation{State: state, ObservedUID: current.UID, WriteOutcome: outcome, Reason: reason, RetryAt: u.now().Add(deleteRetryInterval)}
	if err := u.cfg.BorrowedPVCGuard.RecordDeleteObservation(ctx, id, record.Token, observation); err != nil {
		logDeleteStep(u.logger, guardWriteLevel(err), ctx, deleteRecoveryOperation, "storage PVC delete observation write failed",
			id, err, slog.String("prior_cause", causeText(writeErr)))
		return DeleteResult{}, fmt.Errorf("%w: %v", ErrBackendUnavailable, err)
	}
	if writeErr != nil && !current.Deleting {
		return DeleteResult{}, ErrBackendUnavailable
	}
	return DeleteResult{State: publicState, Volume: toVolume(current)}, nil
}

func (u *UseCase) completeDelete(ctx context.Context, record DeleteRecord, observation DeleteObservation) (DeleteResult, error) {
	if err := u.cfg.BorrowedPVCGuard.CompleteDelete(ctx, record.Identity, record.Token, observation); err != nil {
		logDeleteStep(u.logger, guardWriteLevel(err), ctx, deleteRecoveryOperation, "storage PVC delete complete write failed", record.Identity, err)
		return DeleteResult{}, fmt.Errorf("%w: %v", ErrBackendUnavailable, err)
	}
	if observation.State == "identity_changed" {
		return DeleteResult{}, ErrIdentityChanged
	}
	return DeleteResult{State: DeletionGone}, nil
}

func (u *UseCase) deleteUsage(ctx context.Context, namespace, name string) ([]Consumer, error) {
	call, cancel := context.WithTimeout(ctx, 15*time.Second)
	pods, err := u.volumes.ListPodConsumers(call, namespace, name)
	cancel()
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUsageUnknown, err)
	}
	call, cancel = context.WithTimeout(ctx, 15*time.Second)
	notebooks, err := u.volumes.ListNotebookConsumers(call, namespace, name)
	cancel()
	if err != nil && !errors.Is(err, ErrNotInstalled) {
		return nil, fmt.Errorf("%w: %v", ErrUsageUnknown, err)
	}
	return append(pods, notebooks...), nil
}

func (u *UseCase) StepDeleteRecovery(ctx context.Context) (bool, error) {
	if !u.deleteGuardReady() {
		logDeleteStep(u.logger, slog.LevelError, ctx, deleteRecoveryOperation, "storage PVC delete recovery guard unavailable",
			PVCIdentity{}, nil, slog.String("reason", u.guardUnavailableReason()))
		return false, ErrBackendUnavailable
	}
	step, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	record, err := u.cfg.BorrowedPVCGuard.ClaimDeleteRecovery(step, u.cfg.DeleteExecutor, deleteLeaseTTL)
	if err != nil {
		logDeleteStep(u.logger, slog.LevelError, step, deleteRecoveryOperation, "storage PVC delete recovery claim failed", PVCIdentity{}, err)
		return false, err
	}
	if record == nil {
		return false, nil
	}
	// Resume restores the persisted trace once per claim and always opens this
	// pass's run span; the one telemetry.context_* event lands on that span.
	runCtx, span, tev, terr := telemetry.Resume(ctx, record.TelemetryContext, record.TelemetryContextError, deleteRecoveryOperation)
	if span != nil {
		defer span.End()
	}
	switch tev {
	case "telemetry.context_invalid":
		logDeleteStep(u.logger, slog.LevelWarn, runCtx, deleteRecoveryOperation, "storage PVC delete recovery telemetry context invalid",
			record.Identity, terr, slog.String("event", tev))
	case "telemetry.context_missing":
		logDeleteStep(u.logger, slog.LevelDebug, runCtx, deleteRecoveryOperation, "storage PVC delete recovery telemetry context missing",
			record.Identity, nil, slog.String("event", tev))
	}
	_, err = u.advanceDelete(runCtx, *record)
	return true, err
}

// The composition root owns this goroutine's start, cancellation and join.
// Per-ticket errors remain durable; a failed ticket never kills recovery.
func (u *UseCase) RunDeleteRecovery(ctx context.Context) error {
	if !u.deleteGuardReady() {
		logDeleteStep(u.logger, slog.LevelError, ctx, deleteRecoveryOperation, "storage PVC delete recovery worker guard unavailable",
			PVCIdentity{}, nil, slog.String("reason", u.guardUnavailableReason()))
		return ErrBackendUnavailable
	}
	ticker := time.NewTicker(deleteRetryInterval)
	defer ticker.Stop()
	for {
		_, _ = u.StepDeleteRecovery(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}
