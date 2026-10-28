package instance

import (
	"context"
	"testing"
	"time"
)

func TestLifecycleRestartConfirmsFreshNetworkVersion(t *testing.T) {
	repo, network, worker := lifecycleNetworkFixture(t)
	stepCleanupWorker(t, worker)
	first := repo.instance.ActiveOperation
	if first.State == "completed" || first.Execution.Phase != "waiting_network" {
		t.Fatal("network confirmation must be persisted before runtime completion", first)
	}
	if network.confirmed != 1 || repo.instance.Network.ConfirmedSnapshotRevision != 5 {
		t.Fatalf("fresh attachment version was not confirmed: confirmed=%d revision=%d reason=%s", network.confirmed, repo.instance.Network.ConfirmedSnapshotRevision, repo.instance.Network.Reason)
	}

	// Network's independent observer advances the attachment between worker
	// turns, just as it does while the restarted Pod is already attached.
	network.version++
	stepCleanupWorker(t, worker)
	final := repo.instance.ActiveOperation
	if final.State != "completed" || final.Execution.Phase != "done" || final.Execution.CompletionScope != "runtime_verified" || final.Execution.VerifiedPodUID != "new-pod" || final.Execution.NetworkSnapshotRevision != 5 || repo.instance.Observed != Running {
		t.Fatal("restart did not complete against the current Pod and confirmed snapshot", final, repo.instance.Network)
	}
	if network.confirmed != 1 || repo.instance.Version != 7 {
		t.Fatal("re-observation repeated confirmation or changed the accepted command version")
	}
}

func TestLifecycleNetworkObservationFailureDoesNotConfirm(t *testing.T) {
	repo, network, worker := lifecycleNetworkFixture(t)
	// A cached version could still pass the CAS. Failed observation must not
	// authorize that write or seal runtime completion.
	network.version = repo.instance.Network.AttachmentVersion
	network.observeError = ErrNetworkUnavailable
	stepCleanupWorker(t, worker)
	operation := repo.instance.ActiveOperation
	if network.observes != 1 || network.confirms != 0 || repo.instance.Network.ConfirmedSnapshotRevision != 2 || operation.State != "blocked" || operation.Execution.Phase != "waiting_network" || operation.Execution.CompletionScope != "" {
		t.Fatal("failed observation allowed confirmation or completion", network, operation)
	}
}

func TestLifecycleNetworkCompletionRequiresCurrentPodAndSnapshot(t *testing.T) {
	for _, mismatch := range []string{"network-pod", "network-snapshot", "old-runtime-pod"} {
		t.Run(mismatch, func(t *testing.T) {
			repo, network, worker := lifecycleNetworkFixture(t)
			repo.instance.Network.ConfirmedSnapshotRevision = 5
			switch mismatch {
			case "network-pod":
				network.podUID = "another-pod"
			case "network-snapshot":
				network.snapshot = 4
			case "old-runtime-pod":
				worker.provider.(*lifecycleNetworkProvider).observation.PodUID = "old-pod"
			}
			for range 2 {
				network.version++
				stepCleanupWorker(t, worker)
			}
			operation := repo.instance.ActiveOperation
			if operation.State != "blocked" || operation.Execution.Phase != "waiting_network" || operation.Execution.CompletionScope != "" || operation.Execution.VerifiedPodUID != "" || network.confirms != 0 {
				t.Fatal("mismatched runtime/network evidence completed the restart", network, operation)
			}
		})
	}
}

func TestLifecycleNetworkPublishesChangedPodBeforeConfirmation(t *testing.T) {
	repo, network, worker := lifecycleNetworkFixture(t)
	repo.instance.Network.CurrentPods[0].Reference.UID = "old-pod"
	stepCleanupWorker(t, worker)
	operation := repo.instance.ActiveOperation
	if network.observes != 0 || network.confirms != 0 || repo.instance.Network.SnapshotRevision != 6 || repo.instance.Network.CurrentPods[0].Reference.UID != "new-pod" || operation.State == "completed" || operation.Execution.CompletionScope != "" {
		t.Fatal("network consumed a changed Pod before its submission was persisted", network, operation)
	}
}

type lifecycleNetworkRepo struct{ cleanupWorkerRepo }

func (r *lifecycleNetworkRepo) Claim(context.Context, string, time.Duration) (*Work, error) {
	return &Work{Instance: r.instance, Operation: r.instance.ActiveOperation}, nil
}

type lifecycleNetworkProvider struct {
	*cleanupWorkerProvider
	LifecycleProvider
}

func (p *lifecycleNetworkProvider) ObserveIdentity(ctx context.Context, i Instance) (Observation, error) {
	return p.Observe(ctx, i)
}

func (p *lifecycleNetworkProvider) VerifyRuntime(_ context.Context, _ Instance, o Observation) (Observation, error) {
	return o, nil
}

type lifecycleNetworkPort struct {
	NetworkPort
	version, snapshot             int64
	podUID                        string
	observeError                  error
	observes, confirms, confirmed int
}

func (n *lifecycleNetworkPort) Observe(context.Context, Instance) (NetworkObservation, error) {
	n.observes++
	if n.observeError != nil {
		return NetworkObservation{}, n.observeError
	}
	return NetworkObservation{State: "attached", Version: n.version, SnapshotRevision: n.snapshot, PodUID: n.podUID}, nil
}

func (n *lifecycleNetworkPort) Confirm(_ context.Context, i Instance) error {
	n.confirms++
	if i.Network.AttachmentVersion != n.version || i.Network.SnapshotRevision != n.snapshot || i.PodUID != n.podUID {
		return ErrNetworkConflict
	}
	n.confirmed++
	n.version++
	return nil
}

func lifecycleNetworkFixture(t *testing.T) (*lifecycleNetworkRepo, *lifecycleNetworkPort, *Worker) {
	t.Helper()
	root := WorkloadReference{"kubeflow.org/v1", "Notebook", "tenant", "notebook", "notebook-uid"}
	controller := WorkloadReference{"apps/v1", "StatefulSet", "tenant", "notebook", "controller-uid"}
	pod := WorkloadReference{"v1", "Pod", "tenant", "notebook-0", "new-pod"}
	repo := &lifecycleNetworkRepo{cleanupWorkerRepo: cleanupWorkerRepo{instance: Instance{
		Spec:    Spec{ComputeType: TypeCPU, ClusterID: "cluster", Namespace: "tenant", NotebookName: "notebook"},
		Desired: Running, RuntimeDesired: Running, Observed: Running, Version: 7,
		NotebookUID: "notebook-uid", StatefulSetUID: "controller-uid", PodUID: "new-pod", PodName: "notebook-0",
		MountsInitialized: true, AppliedMountVersion: 1, LastOperationID: "restart-operation",
		ActiveOperation: &Operation{ID: "restart-operation", Action: "restart", State: "accepted", Version: 7, Execution: &Execution{
			SchemaVersion: 1, Phase: "waiting_network", PhaseSequence: 7, BeforeRunState: Running, FinalRunState: Running,
			BeforePods: []PodRef{{"cluster", "tenant", "notebook-0", "old-pod", Container}},
		}},
		Network: &NetworkRuntime{
			State: "open", DesiredRunState: Running, SubmissionID: "submission", Generation: 1,
			SnapshotRevision: 5, ConfirmedSnapshotRevision: 2, AttachmentID: "attachment", AttachmentVersion: 475,
			RootController: root, Controllers: []ControllerReference{{controller, root}}, CurrentPods: []PodReference{{pod, controller}},
		},
	}}}
	provider := &lifecycleNetworkProvider{cleanupWorkerProvider: &cleanupWorkerProvider{observation: Observation{
		NotebookExists: true, NotebookUID: "notebook-uid", NotebookRV: "notebook-rv", StatefulSetUID: "controller-uid",
		PodExists: true, PodReady: true, PodUID: "new-pod", PodName: "notebook-0",
	}}}
	network := &lifecycleNetworkPort{version: 561, snapshot: 5, podUID: "new-pod"}
	worker, err := NewWorker(repo, provider, "lifecycle-network-worker")
	if err != nil {
		t.Fatal(err)
	}
	if err := worker.UseNetwork(network); err != nil {
		t.Fatal(err)
	}
	return repo, network, worker
}
