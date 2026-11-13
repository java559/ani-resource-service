package storage

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"
)

// detailFixture builds a use case whose log output is captured for assertions.
func detailFixture(t *testing.T) (*UseCase, *fakeVolumes, *bytes.Buffer) {
	t.Helper()
	volumes := newFake()
	var out bytes.Buffer
	uc, err := NewUseCase(volumes, nil, Config{
		NamespacePrefix: testPrefix, ClusterID: testTenant, DeleteExecutor: "test-storage",
		Logger: slog.New(slog.NewJSONHandler(&out, nil)),
	})
	if err != nil {
		t.Fatal(err)
	}
	volumes.records["data"] = &VolumeRecord{
		Name: "data", UID: "pvc-uid-1", ResourceVersion: "7", StorageClassName: "fast",
		Labels: map[string]string{LabelManagedBy: ManagedByValue, LabelTenantID: testTenant},
	}
	return uc, volumes, &out
}

// ST05: Namespace/PVC main read failures and ownership mismatches fail the
// whole request; no partial success detail is ever returned.
func TestVolumeDetailMainReadFailureFailsWholeRequest(t *testing.T) {
	uc, volumes, _ := detailFixture(t)
	ctx := context.Background()
	volumes.getErr = fmt.Errorf("%w: %v", ErrBackendUnavailable, errors.New("apiserver connection refused"))
	if _, err := uc.GetVolume(ctx, testTenant, "data"); !errors.Is(err, ErrBackendUnavailable) {
		t.Fatalf("main read failure must fail the request, got %v", err)
	}
	delete(volumes.records, "data")
	volumes.getErr = nil
	if _, err := uc.GetVolume(ctx, testTenant, "data"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing PVC must fail the request, got %v", err)
	}
	volumes.namespace.ManagedBy = "other"
	volumes.records["data"] = &VolumeRecord{Name: "data", UID: "pvc-uid-1", Labels: map[string]string{LabelManagedBy: "other", LabelTenantID: testTenant}}
	if _, err := uc.GetVolume(ctx, testTenant, "data"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("ownership mismatch must fail the request, got %v", err)
	}
}

// ST06: both auxiliary groups succeed and are empty; known=true, error_code=""
// and the empty collections stay non-nil.
func TestVolumeDetailEmptyGroupsStayKnown(t *testing.T) {
	uc, _, _ := detailFixture(t)
	detail, err := uc.GetVolume(context.Background(), testTenant, "data")
	if err != nil {
		t.Fatal(err)
	}
	if !detail.EventsKnown || !detail.ConsumersKnown {
		t.Fatal("empty successful groups must stay known", detail.EventsKnown, detail.ConsumersKnown)
	}
	if detail.EventsErrorCode != "" || detail.ConsumersErrorCode != "" {
		t.Fatal("successful groups must carry empty error codes", detail.EventsErrorCode, detail.ConsumersErrorCode)
	}
	if detail.Events == nil || detail.Consumers == nil || len(detail.Events) != 0 || len(detail.Consumers) != 0 {
		t.Fatal("empty groups must be empty collections, not nil", detail.Events, detail.Consumers)
	}
}

// ST07: a failed group degrades only its own known/error_code; the PVC main
// information and the other group still come back.
func TestVolumeDetailGroupFailureDegradesOnlyOwnGroup(t *testing.T) {
	uc, volumes, _ := detailFixture(t)
	ctx := context.Background()
	volumes.eventErr = fmt.Errorf("%w: %v", ErrBackendUnavailable, errors.New("events apiserver timeout"))
	detail, err := uc.GetVolume(ctx, testTenant, "data")
	if err != nil {
		t.Fatal(err)
	}
	if detail.EventsKnown || detail.EventsErrorCode != VolumeEventsGroupUnavailableCode || len(detail.Events) != 0 {
		t.Fatal("events group must degrade alone", detail.EventsKnown, detail.EventsErrorCode)
	}
	if !detail.ConsumersKnown || detail.ConsumersErrorCode != "" {
		t.Fatal("consumers group must stay known", detail.ConsumersKnown)
	}

	uc2, volumes2, _ := detailFixture(t)
	volumes2.podErr = errors.New("pod list forbidden")
	detail2, err := uc2.GetVolume(ctx, testTenant, "data")
	if err != nil {
		t.Fatal(err)
	}
	if detail2.ConsumersKnown || detail2.ConsumersErrorCode != VolumeConsumersGroupUnavailableCode || len(detail2.Consumers) != 0 {
		t.Fatal("consumers group must degrade alone", detail2.ConsumersKnown, detail2.ConsumersErrorCode)
	}
	if !detail2.EventsKnown || detail2.EventsErrorCode != "" {
		t.Fatal("events group must stay known", detail2.EventsKnown)
	}

	uc3, volumes3, _ := detailFixture(t)
	volumes3.podErr = errors.New("pod list forbidden")
	volumes3.eventErr = errors.New("events apiserver timeout")
	detail3, err := uc3.GetVolume(ctx, testTenant, "data")
	if err != nil {
		t.Fatal("double group failure must not fail the request", err)
	}
	if detail3.EventsKnown || detail3.ConsumersKnown {
		t.Fatal("both failed groups must be unknown")
	}
	if detail3.EventsErrorCode != VolumeEventsGroupUnavailableCode || detail3.ConsumersErrorCode != VolumeConsumersGroupUnavailableCode {
		t.Fatal("both fixed codes must be reported")
	}
	if detail3.Volume.UID != "pvc-uid-1" || detail3.Volume.Name != "data" {
		t.Fatal("PVC main information must survive group failures", detail3.Volume)
	}
}

// ST08: a Notebook listing failure drops the partial consumer set to an empty
// unknown group, while the ErrNotInstalled exception keeps the Pod result.
func TestVolumeDetailNotebookFailureDropsPartialConsumers(t *testing.T) {
	uc, volumes, _ := detailFixture(t)
	volumes.pods = []Consumer{{Kind: "Pod", Namespace: testNS, Name: "worker", Claim: "data"}}
	volumes.notebookErr = errors.New("notebook list forbidden")
	detail, err := uc.GetVolume(context.Background(), testTenant, "data")
	if err != nil {
		t.Fatal(err)
	}
	if detail.ConsumersKnown || detail.ConsumersErrorCode != VolumeConsumersGroupUnavailableCode || len(detail.Consumers) != 0 {
		t.Fatal("partial consumer sets must not be returned", detail.Consumers, detail.ConsumersKnown)
	}

	uc2, volumes2, _ := detailFixture(t)
	volumes2.pods = []Consumer{{Kind: "Pod", Namespace: testNS, Name: "worker", Claim: "data"}}
	volumes2.notebookErr = ErrNotInstalled
	detail2, err := uc2.GetVolume(context.Background(), testTenant, "data")
	if err != nil {
		t.Fatal(err)
	}
	if !detail2.ConsumersKnown || detail2.ConsumersErrorCode != "" || len(detail2.Consumers) != 1 {
		t.Fatal("ErrNotInstalled must keep the Pod result known", detail2.Consumers, detail2.ConsumersKnown)
	}
}

// ST12: the reclaim display reads strictly follow C07 — bound PV wins, a PV
// failure never falls back to the StorageClass, and an explicit class name is
// only consulted when no PV is bound.
func TestVolumeDetailReclaimPolicyMatrix(t *testing.T) {
	ctx := context.Background()

	uc, volumes, _ := detailFixture(t)
	volumes.pv = &PVRecord{Name: "pv-1", ReclaimPolicy: "Retain"}
	volumes.records["data"].PVName = "pv-1"
	detail, err := uc.GetVolume(ctx, testTenant, "data")
	if err != nil || detail.Volume.ReclaimPolicy != "Retain" || !detail.Volume.ReclaimPolicyKnown {
		t.Fatal("bound PV policy must win", detail.Volume.ReclaimPolicy, detail.Volume.ReclaimPolicyKnown, err)
	}
	if volumes.classGets != 0 {
		t.Fatal("bound PV path must not read StorageClasses", volumes.classGets)
	}

	uc2, volumes2, _ := detailFixture(t)
	volumes2.records["data"].PVName = "pv-1"
	detail2, err := uc2.GetVolume(ctx, testTenant, "data")
	if err != nil || detail2.Volume.ReclaimPolicyKnown || detail2.Volume.ReclaimPolicy != "" {
		t.Fatal("PV failure must be unknown without class fallback", detail2.Volume.ReclaimPolicy, detail2.Volume.ReclaimPolicyKnown, err)
	}
	if volumes2.classGets != 0 {
		t.Fatal("PV failure must not fall back to StorageClasses", volumes2.classGets)
	}

	uc3, volumes3, _ := detailFixture(t)
	volumes3.records["data"].PVName = ""
	detail3, err := uc3.GetVolume(ctx, testTenant, "data")
	if err != nil || detail3.Volume.ReclaimPolicy != "Delete" || !detail3.Volume.ReclaimPolicyKnown {
		t.Fatal("explicit class configuration must be displayed", detail3.Volume.ReclaimPolicy, detail3.Volume.ReclaimPolicyKnown, err)
	}

	uc4, volumes4, _ := detailFixture(t)
	volumes4.records["data"].PVName, volumes4.records["data"].StorageClassName = "", ""
	detail4, err := uc4.GetVolume(ctx, testTenant, "data")
	if err != nil || detail4.Volume.ReclaimPolicyKnown || detail4.Volume.ReclaimPolicy != "" {
		t.Fatal("both empty must stay unknown", detail4.Volume.ReclaimPolicy, detail4.Volume.ReclaimPolicyKnown, err)
	}
	if volumes4.classGets != 0 {
		t.Fatal("no default StorageClass may be consulted", volumes4.classGets)
	}

	uc5, volumes5, _ := detailFixture(t)
	volumes5.pv = &PVRecord{Name: "pv-1"}
	volumes5.records["data"].PVName = "pv-1"
	detail5, err := uc5.GetVolume(ctx, testTenant, "data")
	if err != nil || detail5.Volume.ReclaimPolicyKnown {
		t.Fatal("empty PV policy must stay unknown", detail5.Volume.ReclaimPolicyKnown, err)
	}
}

// ST13: the plain PVC read no longer touches StorageClasses anywhere; the
// guarded delete advances by its original rules on a class-bearing PVC.
func TestDeletePathNeverReadsStorageClass(t *testing.T) {
	volumes := &deleteBoundaryVolumes{fakeVolumes: newFake()}
	guard := newTestDeleteGuard()
	var out bytes.Buffer
	uc, err := NewUseCase(volumes, nil, Config{
		NamespacePrefix: testPrefix, ClusterID: testTenant, DeleteExecutor: "test-storage",
		BorrowedPVCGuard: guard, Logger: slog.New(slog.NewJSONHandler(&out, nil)),
	})
	if err != nil {
		t.Fatal(err)
	}
	volumes.records["data"] = &VolumeRecord{
		Name: "data", UID: "pvc-uid-1", StorageClassName: "fast",
		Labels: map[string]string{LabelManagedBy: ManagedByValue, LabelTenantID: testTenant},
	}
	result, err := uc.DeleteVolume(context.Background(), testTenant, "data", "pvc-uid-1")
	if err != nil || result.State != DeletionGone || guard.completeCalls != 1 {
		t.Fatal("guarded delete on a class-bearing PVC must advance", result.State, err)
	}
	if volumes.classGets != 0 {
		t.Fatal("the delete path must not read StorageClasses", volumes.classGets)
	}
}

// ST17: a rejected DELETE followed by an absent GET still completes the ticket
// and reports GONE; the write error must already be logged before that GET.
func TestDeleteRejectedThenAbsentStillReportsGone(t *testing.T) {
	u, volumes, guard, id := guardedDeleteFixture(t)
	var out bytes.Buffer
	u.logger = slog.New(slog.NewJSONHandler(&out, nil))
	volumes.onDelete = func(name, uid string) error {
		delete(volumes.records, name)
		return fmt.Errorf("%w: %w", ErrAccessDenied, errors.New("pvc provisioner denied the delete"))
	}
	result, err := u.DeleteVolume(context.Background(), id.TenantID, id.PVCName, id.TargetUID)
	if err != nil || result.State != DeletionGone || guard.completeCalls != 1 {
		t.Fatal("rejected write followed by absent PVC must report GONE", result.State, err)
	}
	logs := out.String()
	if !strings.Contains(logs, "storage PVC delete rejected") || !strings.Contains(logs, "pvc provisioner denied the delete") {
		t.Fatal("the rejected write was not logged before the observation GET", logs)
	}
}

// ST18①: when the observation write fails after an unknown delete outcome the
// public result stays BackendUnavailable, with both the prior outcome and the
// real persistence failure locatable in the logs.
func TestUnknownWriteThenObservationWriteFailureStaysBackendUnavailable(t *testing.T) {
	u, volumes, guard, id := guardedDeleteFixture(t)
	var out bytes.Buffer
	u.logger = slog.New(slog.NewJSONHandler(&out, nil))
	volumes.onDelete = func(string, string) error { return context.DeadlineExceeded }
	guard.recordErr = errors.New("owner database down")
	if _, err := u.DeleteVolume(context.Background(), id.TenantID, id.PVCName, id.TargetUID); !errors.Is(err, ErrBackendUnavailable) {
		t.Fatal("observation write failure must stay BackendUnavailable", err)
	}
	logs := out.String()
	if !strings.Contains(logs, "storage PVC delete observation write failed") {
		t.Fatal("persistence failure was not logged", logs)
	}
	if !strings.Contains(logs, "context deadline exceeded") || !strings.Contains(logs, "owner database down") {
		t.Fatal("both the prior outcome and the real DB cause must be locatable", logs)
	}
}

// ST18②: a failed CompleteDelete after an absent PVC must not return GONE;
// the persistence failure surfaces as BackendUnavailable with its real cause.
func TestCompleteWriteFailureDoesNotReportGone(t *testing.T) {
	u, volumes, guard, id := guardedDeleteFixture(t)
	var out bytes.Buffer
	u.logger = slog.New(slog.NewJSONHandler(&out, nil))
	guard.completeErr = errors.New("owner database down")
	if _, err := u.DeleteVolume(context.Background(), id.TenantID, id.PVCName, id.TargetUID); !errors.Is(err, ErrBackendUnavailable) {
		t.Fatal("failed completion must stay BackendUnavailable", err)
	}
	if guard.records[deleteTestKey(id)].Phase == "completed" {
		t.Fatal("failed completion must not mark the ticket completed")
	}
	if !strings.Contains(out.String(), "storage PVC delete complete write failed") || !strings.Contains(out.String(), "owner database down") {
		t.Fatal("completion failure was not logged with its real cause", out.String())
	}
	_ = volumes
}

// The lease-expired invariant downgrade stays DEBUG (ST21: normal lease expiry
// never logs ERROR), while real invariant violations stay ERROR.
func TestAdvanceDeleteInvalidRecordLevels(t *testing.T) {
	_, _, guard, id := guardedDeleteFixture(t)
	var out bytes.Buffer
	record, _, err := guard.ReserveDelete(context.Background(), id, "test-storage", deleteLeaseTTL)
	if err != nil {
		t.Fatal(err)
	}
	record.Token.LeaseOwner = "other-executor"
	u := &UseCase{cfg: Config{NamespacePrefix: testPrefix, ClusterID: testTenant, DeleteExecutor: "test-storage"}, now: time.Now, logger: slog.New(slog.NewJSONHandler(&out, nil)), volumes: newFake()}
	if _, err := u.advanceDelete(context.Background(), record); !errors.Is(err, ErrBackendUnavailable) {
		t.Fatal("invalid record must stay BackendUnavailable", err)
	}
	if !strings.Contains(out.String(), "lease_owner_mismatch") || !strings.Contains(out.String(), "ERROR") {
		t.Fatal("real invariant violation must be ERROR with its reason", out.String())
	}
}
