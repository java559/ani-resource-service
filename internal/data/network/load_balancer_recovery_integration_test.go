package data_test

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
	"k8s.io/client-go/tools/clientcmd"
)

// These component boundaries retain real PG admission, production lease/pending
// writes, the actual KC adapter and two distinct Worker owners. Only the external
// HTTP response is withheld. Full process termination remains covered by the
// Gateway matrix, where generated-resource and address cleanup are different.
func TestLBComponentsRecoverResponseLossWithFreshWorkers(t *testing.T) {
	for _, tc := range []struct{ name, method, resource, kind string }{
		{"backend_POST_response_lost", http.MethodPost, "backends", "backend"},
		{"policy_POST_response_lost", http.MethodPost, "backendtrafficpolicies", "policy"},
		{"route_UPDATE_response_lost", http.MethodPatch, "httproutes", "route"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fault := newBaseProcessFault(tc.method, tc.resource, false)
			controller := &lbControllerFixture{}
			f := newLBAdmissionFixture(t, lbProcessIntercept(fault, controller))
			t.Cleanup(fault.unblock)
			// Replace the setup observer before starting this recovery runtime;
			// its index must not outlive the repository being discarded below.
			f.f.stopObservation()
			expected := seedLBInstallation(f.api)
			pods := lbRecoveryProviderPods(f)
			first, firstOwner, stopFirst := lbRecoveryWorker(t, f, f.f.p, expected)
			awaitNET05A(t, 5*time.Second, func() bool {
				var ready bool
				return f.f.owner.QueryRow(f.f.ctx, `SELECT ready FROM network_lb_capabilities WHERE cluster_id='test-cluster' AND fingerprint=$1`, expected.Fingerprint).Scan(&ready) == nil && ready
			})
			if tc.method == http.MethodPost {
				fault.armed.Store(true)
			}
			created, err := f.lbs.Create(f.f.ctx, f.request)
			if err != nil {
				t.Fatal(err)
			}
			id := created.LoadBalancer.ID
			if tc.method == http.MethodPatch {
				lb := lbRecoveryState(t, f, id, 1, first)
				fault.armed.Store(true)
				zero := uint32(0)
				_, err = f.lbs.Update(f.f.ctx, biz.UpdateLoadBalancer{ID: id, ExpectedVersion: lb.Version, IdempotencyKey: "weight-zero", LoadBalancerMutableInput: biz.LoadBalancerMutableInput{Health: f.request.Health, Name: "updated", Backends: []biz.LoadBalancerBackendInput{{ID: lb.Backends[0].ID, SubnetID: lb.Backends[0].SubnetID, Address: lb.Backends[0].Address, Port: lb.Backends[0].Port, Weight: &zero}}}})
				if err != nil {
					t.Fatal(err)
				}
			}
			stepCtx, cancelStep := context.WithCancel(f.f.ctx)
			done := make(chan error, 1)
			go func() {
				for {
					if _, err := first.Step(stepCtx); err != nil {
						done <- err
						return
					}
					select {
					case <-stepCtx.Done():
						done <- stepCtx.Err()
						return
					case <-time.After(2 * time.Millisecond):
					}
				}
			}()
			t.Cleanup(cancelStep)
			select {
			case <-fault.reached:
			case err := <-done:
				t.Fatalf("Worker stopped before %s: %v", tc.name, err)
			case <-time.After(8 * time.Second):
				t.Fatal("Worker did not reach component response-loss boundary")
			}
			if fault.status.Load() < 200 || fault.status.Load() >= 300 {
				t.Fatal("actual adapter write did not succeed", fault.status.Load())
			}
			var namespace, name, pending, leaseOwner string
			var dispatched bool
			var epoch int64
			if err = f.f.owner.QueryRow(f.f.ctx, `SELECT c.namespace,c.provider_name,c.pending_action,c.create_dispatched,r.lease_owner::text,r.lease_epoch FROM network_lb_components c JOIN network_reconciliations r USING(tenant_id,lb_id) WHERE c.tenant_id=$1 AND c.lb_id=$2 AND c.kind=$3`, f.f.tenant, id, tc.kind).Scan(&namespace, &name, &pending, &dispatched, &leaseOwner, &epoch); err != nil {
				t.Fatal(err)
			}
			wantAction := "create"
			if tc.method == http.MethodPatch {
				wantAction = "update"
			}
			if pending != wantAction || !dispatched || leaseOwner != firstOwner || epoch <= 0 {
				t.Fatal("Worker did not durably fence the external write", pending, dispatched, leaseOwner, epoch)
			}
			written := f.api.Object(tc.resource, namespace, name)
			if written == nil {
				t.Fatal("successful adapter write left no Provider object")
			}
			writtenUID := written["metadata"].(map[string]any)["uid"].(string)
			lbRecoveryOccupancy(t, f, id, 1, 2)
			// Finish intentionally ignores caller cancellation. Close the actual
			// old runtime before canceling HTTP, so its detached completion fails
			// against a closed pool and leaves the real lease/pending write intact.
			// No test SQL manufactures pending state or ownership.
			stopFirst()
			cancelStep()
			select {
			case err := <-done:
				cause := errors.Unwrap(err)
				if biz.ReasonOf(err) != biz.DependencyUnavailable || cause == nil || !strings.Contains(cause.Error(), "closed") || !strings.Contains(cause.Error(), "pool") {
					t.Fatal("unfinished Worker did not fail completion against its closed pool", err)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("unfinished Worker did not exit")
			}
			first, f.f.w = nil, nil
			secondRepo := lbRecoveryRepository(t, f)
			thirdRepo := lbRecoveryRepository(t, f)
			second, secondOwner, _ := lbRecoveryWorker(t, f, secondRepo, expected)
			third, thirdOwner, _ := lbRecoveryWorker(t, f, thirdRepo, expected)
			if secondOwner == firstOwner || thirdOwner == firstOwner || secondOwner == thirdOwner {
				t.Fatal("recovery reused a Worker owner")
			}
			f.f.p = secondRepo
			f.lbs, err = biz.NewLoadBalancers(secondRepo, biz.ContextEgressAuthorization{}, []byte(strings.Repeat("c", 32)), time.Minute, time.Now)
			if err != nil {
				t.Fatal(err)
			}
			// Fresh owners may work on other due resources, but cannot replace
			// the abandoned LB owner before its genuine database lease expires.
			var remaining bool
			var recoveredOwner string
			var recoveredEpoch int64
			if err = f.f.owner.QueryRow(f.f.ctx, `SELECT c.pending_action,r.lease_owner::text,r.lease_epoch,r.lease_until>clock_timestamp() FROM network_lb_components c JOIN network_reconciliations r USING(tenant_id,lb_id) WHERE c.tenant_id=$1 AND c.lb_id=$2 AND c.kind=$3`, f.f.tenant, id, tc.kind).Scan(&pending, &recoveredOwner, &recoveredEpoch, &remaining); err != nil || pending != wantAction || recoveredOwner != firstOwner || recoveredEpoch != epoch || !remaining {
				t.Fatal("closed runtime did not preserve its pending write and live lease", pending, recoveredOwner, recoveredEpoch, remaining, err)
			}
			for _, worker := range []*biz.Worker{second, third} {
				if _, err := worker.Step(f.f.ctx); err != nil {
					t.Fatal(err)
				}
			}
			if err = f.f.owner.QueryRow(f.f.ctx, `SELECT lease_owner::text,lease_epoch FROM network_reconciliations WHERE tenant_id=$1 AND lb_id=$2`, f.f.tenant, id).Scan(&recoveredOwner, &recoveredEpoch); err != nil || recoveredOwner != firstOwner || recoveredEpoch != epoch {
				t.Fatal("fresh Worker took over an unexpired lease", recoveredOwner, recoveredEpoch, err)
			}
			fault.unblock()
			wantVersion := int64(1)
			if tc.method == http.MethodPatch {
				wantVersion = 2
			}
			lb := lbRecoveryState(t, f, id, wantVersion, second, third)
			var uid string
			if err = f.f.owner.QueryRow(f.f.ctx, `SELECT provider_uid,pending_action FROM network_lb_components WHERE tenant_id=$1 AND lb_id=$2 AND kind=$3`, f.f.tenant, id, tc.kind).Scan(&uid, &pending); err != nil || uid != writtenUID || pending != "" {
				t.Fatal("fresh Worker did not adopt original UID and clear pending", uid, writtenUID, pending, err)
			}
			if tc.method == http.MethodPatch {
				route := f.api.Object(tc.resource, namespace, name)
				refs := route["spec"].(map[string]any)["rules"].([]any)[0].(map[string]any)["backendRefs"].([]any)
				if len(refs) != 1 || refs[0].(map[string]any)["weight"] != float64(0) || lb.Backends[0].Weight != 0 {
					t.Fatal("recovered CAS update did not retain zero weight", route, lb)
				}
			}
			lbRecoveryOccupancy(t, f, id, 1, 2)
			deleted, err := f.lbs.Delete(f.f.ctx, "", id)
			if err != nil {
				t.Fatal(err)
			}
			lbRecoveryOccupancy(t, f, id, 1, 2)
			awaitNET05A(t, 8*time.Second, func() bool {
				for _, worker := range []*biz.Worker{second, third} {
					if _, err := worker.Step(f.f.ctx); err != nil {
						t.Fatal(err)
					}
				}
				lb, err = f.lbs.Get(f.f.ctx, "", id)
				return err == nil && lb.State == biz.Deleted
			})
			operation, err := f.lbs.GetOperation(f.f.ctx, "", deleted.Operation.ID)
			if err != nil || operation.State != biz.Succeeded {
				t.Fatal("product cleanup did not complete", operation, err)
			}
			lbRecoveryOccupancy(t, f, id, 0, 0)
			if lb.DataPlaneState != "unknown" || lb.DataPlaneObservedAt != nil {
				t.Fatal("component recovery asserted live traffic health", lb)
			}
			controller.mu.Lock()
			posts := map[string]int{}
			for _, request := range controller.requests {
				if strings.HasPrefix(request, "POST/") {
					posts[strings.Split(request, "/")[1]]++
				}
			}
			controller.mu.Unlock()
			for _, kind := range []string{"backends", "gateways", "httproutes", "backendtrafficpolicies"} {
				if posts[kind] != 1 {
					t.Fatal("duplicate Provider create after component recovery", kind, posts)
				}
			}
			for key, uid := range pods {
				parts := strings.SplitN(key, "/", 3)
				pod := f.api.Object("pods", parts[1], parts[2])
				if pod == nil || pod["metadata"].(map[string]any)["uid"] != uid {
					t.Fatal("cleanup changed a Provider or instance-owner Pod", key)
				}
			}
			t.Logf("%s recovered original component UID using new repositories and Worker owners; product cleanup verified", tc.name)
		})
	}
}

func lbRecoveryRepository(t *testing.T, f *lbAdmissionFixture) *data.Postgres {
	t.Helper()
	repository, err := data.OpenPostgres(f.f.ctx, f.runtimeDSN, data.Placement{ClusterID: "test-cluster", NamespacePrefix: "tenant-"})
	if err != nil {
		t.Fatal(err)
	}
	configureRepositoryVPCPresets(t, repository)
	t.Cleanup(repository.Close)
	return repository
}

func lbRecoveryWorker(t *testing.T, f *lbAdmissionFixture, repository *data.Postgres, expected data.LoadBalancerInstallation) (*biz.Worker, string, func()) {
	t.Helper()
	config, err := clientcmd.BuildConfigFromFlags("", f.kubeconfig)
	if err != nil {
		t.Fatal(err)
	}
	config.QPS, config.Burst = 1000, 1000
	provider, err := data.NewKCProvider(repository, config)
	if err != nil {
		t.Fatal(err)
	}
	repository.UseEgressInfrastructure(provider)
	if err = provider.ConfigureLoadBalancer(expected); err != nil {
		t.Fatal(err)
	}
	options := data.DefaultObservationOptions()
	options.AuditInterval, options.AuditJitter, options.FlushInterval = 80*time.Millisecond, time.Millisecond, 10*time.Millisecond
	observer, err := provider.EnableObservation(options)
	if err != nil {
		t.Fatal(err)
	}
	live, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- observer.Start(live) }()
	var stopOnce sync.Once
	stop := func() {
		stopOnce.Do(func() {
			cancel()
			if err := <-done; err != nil {
				t.Error(err)
			}
			repository.Close()
		})
	}
	t.Cleanup(stop)
	policy := biz.DefaultWorkerPolicy()
	policy.RequestTimeout, policy.Lease = time.Second, 4*time.Second
	policy.ObserveEvery, policy.RetryMin, policy.RetryMax = 100*time.Millisecond, 5*time.Millisecond, 20*time.Millisecond
	owner := uuid.NewString()
	worker, err := biz.NewWorker(repository, provider, owner, policy)
	if err != nil {
		t.Fatal(err)
	}
	return worker, owner, stop
}

func lbRecoveryState(t *testing.T, f *lbAdmissionFixture, id string, version int64, workers ...*biz.Worker) biz.LoadBalancer {
	t.Helper()
	var lb biz.LoadBalancer
	awaitNET05A(t, 8*time.Second, func() bool {
		for _, worker := range workers {
			if _, err := worker.Step(f.f.ctx); err != nil {
				t.Fatal(err)
			}
		}
		var err error
		lb, err = f.lbs.Get(f.f.ctx, "", id)
		return err == nil && lb.State == biz.Available && lb.AppliedVersion == version
	})
	return lb
}

func lbRecoveryOccupancy(t *testing.T, f *lbAdmissionFixture, id string, vips, refs int) {
	t.Helper()
	for table, want := range map[string]int{"network_lb_vip_intents": vips, "network_lb_subnet_refs": refs} {
		var active int
		if err := f.f.owner.QueryRow(f.f.ctx, "SELECT count(*) FROM "+table+" WHERE tenant_id=$1 AND lb_id=$2 AND released_at IS NULL", f.f.tenant, id).Scan(&active); err != nil || active != want {
			t.Fatal("component recovery changed occupancy", table, active, want, err)
		}
	}
}

func lbRecoveryProviderPods(f *lbAdmissionFixture) map[string]string {
	pods := map[string]string{}
	f.api.Backend.Mu.Lock()
	defer f.api.Backend.Mu.Unlock()
	for key, object := range f.api.Backend.Objects {
		if strings.HasPrefix(key, "pods/") {
			pods[key] = object["metadata"].(map[string]any)["uid"].(string)
		}
	}
	return pods
}
