package vm

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

const (
	cloneTestTenant  = "64c5c35a-f554-432c-b4bc-7b4f36f08420"
	cloneTestTarget  = "21cf875f-781a-41db-81cf-7063f4f4cfc4"
	cloneTestSource  = "31cf875f-781a-41db-81cf-7063f4f4cfc5"
	cloneTestOp      = "b492e0ba-f34e-4143-a9dc-94427c2d3bec"
	cloneTestNativeN = "vm-target"
)

// cloneTestRepo is the durable fake: Claim returns the last saved state so one
// test can drive many worker ticks.
type cloneTestRepo struct {
	op       Operation
	vm       VM
	events   *[]string
	releases []CloneSourceRelease
}

func (r *cloneTestRepo) AcceptCreate(context.Context, Acceptance) (Operation, error) {
	return Operation{}, ErrInvalid
}
func (r *cloneTestRepo) AcceptDelete(context.Context, Acceptance) (Operation, error) {
	return Operation{}, ErrInvalid
}
func (r *cloneTestRepo) AcceptCommand(context.Context, Command) (Operation, error) {
	return Operation{}, ErrInvalid
}
func (r *cloneTestRepo) LookupReplay(context.Context, Command) (Operation, error) {
	return Operation{}, ErrNotFound
}
func (r *cloneTestRepo) Get(context.Context, string, string) (VM, error) { return r.vm, nil }
func (r *cloneTestRepo) List(context.Context, string, ListFilter) ([]VM, int64, error) {
	return nil, 0, nil
}
func (r *cloneTestRepo) GetOperation(context.Context, string, string, string) (Operation, error) {
	return r.op, nil
}
func (r *cloneTestRepo) Claim(context.Context, string, time.Duration) (Operation, VM, error) {
	return r.op, r.vm, nil
}
func (r *cloneTestRepo) Save(_ context.Context, o Operation, v VM) error {
	r.op, r.vm = o, v
	return nil
}
func (r *cloneTestRepo) Checkpoint(_ context.Context, o Operation, v VM) error {
	r.op, r.vm = o, v
	return nil
}
func (r *cloneTestRepo) UpdateNetwork(context.Context, VM, NetworkPlan) error { return nil }
func (r *cloneTestRepo) ReleaseCloneSourceHELD(_ context.Context, release CloneSourceRelease) error {
	r.releases = append(r.releases, release)
	*r.events = append(*r.events, "release:"+release.Kind)
	return nil
}

// cloneTestNative scripts the native clone projection and records every step.
type cloneTestNative struct {
	events       *[]string
	observations []CloneObservation
	observeErr   error
	eligibility  CloneEligibility
	eligibilityE error
	deleteResult []CloneDeleteResult
	targetGone   bool
	volumesDone  bool
}

func (n *cloneTestNative) note(event string) { *n.events = append(*n.events, event) }

func (n *cloneTestNative) ValidateSource(context.Context, Source) error { return nil }
func (n *cloneTestNative) EnsureVM(context.Context, VM) (ObjectRef, error) {
	return ObjectRef{}, nil
}
func (n *cloneTestNative) EnsureInitialization(context.Context, VM) (ObjectRef, error) {
	return ObjectRef{}, nil
}
func (n *cloneTestNative) DeleteInitialization(context.Context, VM) (bool, error) {
	return true, nil
}
func (n *cloneTestNative) EnsureVolume(_ context.Context, _ VM, d Disk) (Disk, error) {
	return d, nil
}
func (n *cloneTestNative) Observe(context.Context, VM) (Observation, error) {
	return Observation{}, nil
}
func (n *cloneTestNative) Start(context.Context, VM) error      { return nil }
func (n *cloneTestNative) Stop(context.Context, VM, bool) error { return nil }
func (n *cloneTestNative) Restart(context.Context, VM) error    { return nil }
func (n *cloneTestNative) Delete(context.Context, VM) error     { n.note("delete-vm"); return nil }
func (n *cloneTestNative) DeleteVolume(context.Context, VM, Disk) (bool, error) {
	return true, nil
}

func (n *cloneTestNative) EnsureClone(context.Context, VM, CloneRequest) (CloneObservation, error) {
	n.note("ensure-clone")
	return CloneObservation{Exists: true, UID: "clone-uid", Phase: "SnapshotInProgress", SnapshotName: "snap-1"}, nil
}
func (n *cloneTestNative) ObserveClone(context.Context, VM, CloneRequest) (CloneObservation, error) {
	if n.observeErr != nil {
		return CloneObservation{}, n.observeErr
	}
	if len(n.observations) == 0 {
		return CloneObservation{Exists: false}, nil
	}
	next := n.observations[0]
	n.observations = n.observations[1:]
	return next, nil
}
func (n *cloneTestNative) DeleteClone(context.Context, VM, CloneRequest, string, string) (CloneDeleteResult, error) {
	n.note("delete-clone")
	if len(n.deleteResult) == 0 {
		return CloneDeleteResult{Gone: true}, nil
	}
	next := n.deleteResult[0]
	n.deleteResult = n.deleteResult[1:]
	return next, nil
}
func (n *cloneTestNative) ObserveSourceCloneEligibility(context.Context, VM, VM) (CloneEligibility, error) {
	return n.eligibility, n.eligibilityE
}
func (n *cloneTestNative) RecheckCloneSource(context.Context, VM) (CloneEligibility, error) {
	return n.eligibility, n.eligibilityE
}
func (n *cloneTestNative) ApplyCloneTargetConfiguration(context.Context, VM, CloneRequest) ([]Disk, ObjectRef, error) {
	return nil, ObjectRef{}, ErrInvalid
}
func (n *cloneTestNative) DeleteCloneTarget(context.Context, VM, ObjectRef) (bool, error) {
	n.note("delete-target")
	return n.targetGone, nil
}
func (n *cloneTestNative) DeleteNamedVolumes(context.Context, VM, []VolumeRef) (bool, error) {
	n.note("delete-volumes")
	return n.volumesDone, nil
}

// cloneTestNetwork is both the network and the security-group port.
type cloneTestNetwork struct {
	events *[]string
}

func (n *cloneTestNetwork) Prepare(context.Context, VM) (NetworkPlan, error) {
	*n.events = append(*n.events, "prepare-network")
	return NetworkPlan{AttachmentID: "att-1", SubmissionID: "sub-1"}, nil
}
func (n *cloneTestNetwork) Confirm(context.Context, VM, Observation) error { return nil }
func (n *cloneTestNetwork) CurrentAddresses(context.Context, VM) ([]Address, error) {
	return nil, nil
}
func (n *cloneTestNetwork) Release(context.Context, VM) (bool, error) {
	*n.events = append(*n.events, "release-network")
	return true, nil
}
func (n *cloneTestNetwork) ResolveSecurityGroups(context.Context, string, string, string, []string) ([]string, error) {
	return nil, nil
}
func (n *cloneTestNetwork) SyncSecurityGroupBinding(context.Context, string, string, string, string, string, int64, []string) ([]string, error) {
	return nil, nil
}
func (n *cloneTestNetwork) ConfirmSecurityGroupBinding(context.Context, string, string, string, int64, []string) error {
	return nil
}
func (n *cloneTestNetwork) ReleaseSecurityGroupBinding(context.Context, string, string, string, int64) error {
	*n.events = append(*n.events, "release-security-groups")
	return nil
}

func workerCloneVM() VM {
	v := VM{
		TenantID: cloneTestTenant,
		ID:       cloneTestTarget,
		Version:  3,
		Native:   ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-a", Name: cloneTestNativeN, UID: "target-uid"},
		Spec: Spec{
			Namespace:  "tenant-a",
			Digest:     "digest",
			BootDevice: "SYSTEM_DISK",
			CloneSource: &CloneSource{
				SourceVMID: cloneTestSource,
				SourceName: "vm-source",
				Native:     ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-a", Name: "vm-source", UID: "source-uid"},
				Disks: []CloneDisk{
					{SourceDiskID: "disk-1", VolumeName: "disk-sys", Role: "system", Name: "system", SizeGB: 8},
					{SourceDiskID: "disk-2", VolumeName: "disk-data", Role: "data", Name: "data", SizeGB: 4},
				},
			},
		},
		Network: NetworkPlan{AttachmentID: "att-1", SubmissionID: "sub-1"},
	}
	v.Disks = []Disk{{VolumeName: "disk-sys", Role: "system", Attached: true, Serial: "serial-sys"}}
	return v
}

func workerCloneOp(v VM, action string) Operation {
	return Operation{TenantID: v.TenantID, ID: cloneTestOp, VMID: v.ID, Action: action, State: "running", Key: "key"}
}

func newCloneWorker(t *testing.T, v VM, o Operation, native *cloneTestNative) (*Worker, *cloneTestRepo, *[]string) {
	t.Helper()
	events := &[]string{}
	native.events = events
	repo := &cloneTestRepo{op: o, vm: v, events: events}
	network := &cloneTestNetwork{events: events}
	worker, err := NewWorker(repo, native, network)
	if err != nil {
		t.Fatal(err)
	}
	if err = worker.UseSecurityGroups(network); err != nil {
		t.Fatal(err)
	}
	return worker, repo, events
}

func hasEvent(events []string, want string) bool {
	for _, event := range events {
		if event == want {
			return true
		}
	}
	return false
}

// TestCloneWorkerFailedCleanup covers T1: a failed clone with a usable target
// deletes the clone, releases the HELD, then removes VM, volumes and network.
func TestCloneWorkerFailedCleanup(t *testing.T) {
	ctx := context.Background()
	v := workerCloneVM()
	v.Native = ObjectRef{}
	v.CloneProgress = &CloneProgress{
		Name: CloneName(v.ID), UID: "clone-uid", CloneDeleteRequested: true,
		PendingVolumes: []VolumeRef{
			{VolumeName: "disk-sys", PVCName: "pvc-sys", PVCUID: "pvc-uid-1"},
			{VolumeName: "disk-data", PVCName: "pvc-data", PVCUID: "pvc-uid-2"},
		},
	}
	outputs := []VolumeRef{
		{VolumeName: "disk-sys", PVCName: "pvc-sys", PVCUID: "pvc-uid-1"},
		{VolumeName: "disk-data", PVCName: "pvc-data", PVCUID: "pvc-uid-2"},
	}
	native := &cloneTestNative{
		observations: []CloneObservation{{
			Exists: true, UID: "clone-uid", ResourceVersion: "4", Failed: true, Reason: "boom",
			Target:  ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-a", Name: cloneTestNativeN, UID: "target-uid"},
			Outputs: outputs,
		}},
		targetGone:  true,
		volumesDone: true,
	}
	worker, repo, events := newCloneWorker(t, v, workerCloneOp(v, CreateAction), native)
	for i := 0; i < 12 && repo.op.State != "failed"; i++ {
		if err := worker.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if repo.op.State != "failed" {
		t.Fatalf("failed cleanup did not finish: %s %v", repo.op.State, *events)
	}
	want := []string{"delete-clone", "release:REGISTERED", "delete-target", "delete-volumes", "release-network", "release-security-groups"}
	if strings.Join(*events, ",") != strings.Join(want, ",") {
		t.Fatalf("cleanup order = %v", *events)
	}
	if len(repo.releases) != 1 || repo.releases[0].Kind != "REGISTERED" {
		t.Fatalf("release = %v", repo.releases)
	}
}

// TestCloneWorkerFailedWithoutTarget covers T2: no target and incomplete named
// output keeps the producer and the HELD reference.
func TestCloneWorkerFailedWithoutTarget(t *testing.T) {
	ctx := context.Background()
	v := workerCloneVM()
	v.Native = ObjectRef{}
	v.CloneProgress = &CloneProgress{Name: CloneName(v.ID), UID: "clone-uid", CloneDeleteRequested: true}
	native := &cloneTestNative{observations: []CloneObservation{{
		Exists: true, UID: "clone-uid", Failed: true, Reason: "boom",
		Outputs: []VolumeRef{{VolumeName: "disk-sys", PVCName: "pvc-sys", PVCUID: "pvc-uid-1"}},
	}}}
	worker, repo, events := newCloneWorker(t, v, workerCloneOp(v, CreateAction), native)
	if err := worker.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if repo.op.State != "unknown" {
		t.Fatalf("state = %s", repo.op.State)
	}
	if hasEvent(*events, "delete-clone") || len(repo.releases) != 0 {
		t.Fatalf("residual case was disposed of: %v %v", *events, repo.releases)
	}
}

// TestCloneWorkerVanishedBeforeRegistration covers T3: an unregistered clone
// that vanished without outputs keeps the HELD reference.
func TestCloneWorkerVanishedBeforeRegistration(t *testing.T) {
	ctx := context.Background()
	v := workerCloneVM()
	v.Native = ObjectRef{}
	v.CloneProgress = &CloneProgress{Name: CloneName(v.ID), UID: "clone-uid"}
	native := &cloneTestNative{observations: []CloneObservation{{Exists: false}}}
	worker, repo, events := newCloneWorker(t, v, workerCloneOp(v, CreateAction), native)
	if err := worker.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if repo.op.State != "unknown" || hasEvent(*events, "delete-clone") || len(repo.releases) != 0 {
		t.Fatalf("vanished clone was released: %s %v %v", repo.op.State, *events, repo.releases)
	}
}

// TestCloneWorkerEarlyCancel covers T4: a pre-registration delete removes the
// producer before releasing the source as CANCELLED.
func TestCloneWorkerEarlyCancel(t *testing.T) {
	ctx := context.Background()
	v := workerCloneVM()
	v.Closing = true
	v.Native = ObjectRef{}
	v.CloneProgress = &CloneProgress{Name: CloneName(v.ID), CloneDeleteRequested: true}
	native := &cloneTestNative{observations: []CloneObservation{{
		Exists: true, UID: "clone-uid", ResourceVersion: "2", Pending: true,
	}}}
	worker, repo, events := newCloneWorker(t, v, workerCloneOp(v, DeleteAction), native)
	if err := worker.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if !hasEvent(*events, "delete-clone") {
		t.Fatalf("early cancel did not delete the clone: %v", *events)
	}
	if len(repo.releases) != 1 || repo.releases[0].Kind != "CANCELLED" {
		t.Fatalf("release = %v", repo.releases)
	}
	if repo.vm.CloneProgress == nil || !repo.vm.CloneProgress.CancelHandled {
		t.Fatal("cancel hand-off was not recorded")
	}
}

// TestCloneWorkerCancelNegatives covers T5/T6: every condition that is not a
// provable early cancel must wait, and a foreign UID is an ownership failure.
func TestCloneWorkerCancelNegatives(t *testing.T) {
	ctx := context.Background()
	cases := []struct {
		name        string
		state       CloneObservation
		observeErr  error
		wantState   string
		pending     []VolumeRef
		wantNoClone bool
	}{
		{name: "restore exists", state: CloneObservation{Exists: true, UID: "clone-uid", Pending: true, RestoreName: "restore-1"}, wantState: "running"},
		{name: "snapshot in progress", state: CloneObservation{Exists: true, UID: "clone-uid", Pending: true, SnapshotName: "snap-1"}, wantState: "running"},
		{name: "outputs incomplete", state: CloneObservation{Exists: true, UID: "clone-uid", Pending: true, Outputs: []VolumeRef{{VolumeName: "disk-sys", PVCName: "pvc-sys"}}}, wantState: "running"},
		{name: "failed undetermined", state: CloneObservation{Exists: true, UID: "clone-uid", Failed: true}, wantState: "running"},
		{name: "api unreadable", observeErr: ErrDependency, wantState: "running"},
		{name: "foreign uid", observeErr: ErrOwnership, wantState: "failed"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			v := workerCloneVM()
			v.Closing = true
			v.Native = ObjectRef{}
			v.CloneProgress = &CloneProgress{Name: CloneName(v.ID), CloneDeleteRequested: true, PendingVolumes: test.pending}
			native := &cloneTestNative{observations: []CloneObservation{test.state}, observeErr: test.observeErr}
			worker, repo, events := newCloneWorker(t, v, workerCloneOp(v, DeleteAction), native)
			if err := worker.RunOnce(ctx); err != nil {
				t.Fatal(err)
			}
			if repo.op.State != test.wantState {
				t.Fatalf("state = %s, want %s (%v)", repo.op.State, test.wantState, *events)
			}
			if hasEvent(*events, "delete-clone") || len(repo.releases) != 0 {
				t.Fatalf("cancel advanced on a negative case: %v %v", *events, repo.releases)
			}
		})
	}
}

// TestCloneWorkerUnsubmittedCleanup covers T7: a source rejected before submit
// releases the network hand-off first, then the HELD as CANCELLED.
func TestCloneWorkerUnsubmittedCleanup(t *testing.T) {
	ctx := context.Background()
	v := workerCloneVM()
	v.Native = ObjectRef{}
	v.Network = NetworkPlan{}
	native := &cloneTestNative{eligibility: CloneEligibility{Unsupported: []CloneBlocker{
		{Kind: "UNSUPPORTED", Reason: "source uses dataVolumeTemplates"},
	}}}
	worker, repo, events := newCloneWorker(t, v, workerCloneOp(v, CreateAction), native)
	for i := 0; i < 5 && repo.op.State != "failed"; i++ {
		if err := worker.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
	}
	want := []string{"prepare-network", "release-network", "release-security-groups", "release:CANCELLED"}
	if strings.Join(*events, ",") != strings.Join(want, ",") {
		t.Fatalf("unsubmitted order = %v", *events)
	}
	if repo.op.State != "failed" {
		t.Fatalf("state = %s", repo.op.State)
	}
	if len(repo.releases) != 1 {
		t.Fatalf("release = %v", repo.releases)
	}
	release := repo.releases[0]
	if !strings.HasPrefix(release.Reason, "unsubmitted: ") || release.Evidence != "no submit intent; network released" {
		t.Fatalf("release audit = %+v", release)
	}
	if hasEvent(*events, "ensure-clone") {
		t.Fatal("a rejected source reached submission")
	}
}

// TestCloneEligibilityErrorKinds keeps the two admission answers distinct.
func TestCloneEligibilityErrorKinds(t *testing.T) {
	unsupported := CloneEligibility{Unsupported: []CloneBlocker{{VolumeName: "disk-sys", Reason: "source PVC volumeMode is not Block"}}}
	if err := unsupported.UnsupportedError(); !errors.Is(err, ErrCloneSourceUnavailable) || !strings.Contains(err.Error(), "disk-sys") {
		t.Fatalf("unsupported error = %v", err)
	}
	notReady := CloneEligibility{NotReady: []CloneBlocker{{VolumeName: "disk-sys", Reason: "source PVC is not Bound"}}}
	if err := notReady.NotReadyError(); !errors.Is(err, ErrCloneSourceNotReady) || !strings.Contains(err.Error(), "not Bound") {
		t.Fatalf("not-ready error = %v", err)
	}
	if unsupported.UnsupportedError() == nil || unsupported.NotReadyError() != nil {
		t.Fatal("eligibility error kinds are not separate")
	}
}
