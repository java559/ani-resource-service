//go:build instanceintegration

package instance_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	quotapb "github.com/zhangzhe-ctrl/ani-governance/api/quota/gen/go/quota/service/v1"
	terminal "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/terminal/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	data "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/instance"
	service "github.com/zhangzhe-ctrl/ani-resource-service/internal/service/instance"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

type fixture struct {
	repo    *data.Postgres
	runtime *pgxpool.Pool
	dsn     string
}

func database(t *testing.T) *fixture {
	t.Helper()
	ctx := context.Background()
	raw, e := os.ReadFile(os.Getenv("INSTANCE_TEST_ADMIN_DSN_FILE"))
	if e != nil {
		t.Fatal("exclusive instance database DSN required")
	}
	u, e := url.Parse(string(raw))
	if e != nil {
		t.Fatal("invalid fixture DSN")
	}
	admin, e := pgxpool.New(ctx, string(raw))
	if e != nil {
		t.Fatal("admin unavailable")
	}
	t.Cleanup(admin.Close)
	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")
	name := "instance_test_" + suffix
	owner := "instance_owner_" + suffix
	role := "instance_runtime_" + suffix
	password, _ := u.User.Password()
	literal := "'" + strings.ReplaceAll(password, "'", "''") + "'"
	for _, r := range []string{owner, role} {
		if _, e = admin.Exec(ctx, "CREATE ROLE "+pgx.Identifier{r}.Sanitize()+" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD "+literal); e != nil {
			t.Fatal("role creation failed")
		}
		r := r
		t.Cleanup(func() {
			if _, e := admin.Exec(ctx, "DROP ROLE "+pgx.Identifier{r}.Sanitize()); e != nil {
				t.Error("role cleanup failed")
			}
		})
	}
	if _, e = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()+" OWNER "+pgx.Identifier{owner}.Sanitize()); e != nil {
		t.Fatal("database creation failed")
	}
	t.Cleanup(func() {
		if _, e := admin.Exec(ctx, "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)"); e != nil {
			t.Error("database cleanup failed")
		}
	})
	u.Path = "/" + name
	u.User = url.UserPassword(owner, password)
	if e = data.Migrate(ctx, u.String(), role); e != nil {
		t.Fatal(e)
	}
	if e = data.Migrate(ctx, u.String(), role); e != nil {
		t.Fatal("migration replay", e)
	}
	u.User = url.UserPassword(role, password)
	repo, e := data.OpenPostgres(ctx, u.String())
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(repo.Close)
	runtime, e := pgxpool.New(ctx, u.String())
	if e != nil {
		t.Fatal("runtime unavailable")
	}
	t.Cleanup(runtime.Close)
	return &fixture{repo, runtime, u.String()}
}
func create() biz.Create {
	tenant, id, op := uuid.NewString(), uuid.NewString(), uuid.NewString()
	return biz.Create{TenantID: tenant, ID: id, OperationID: op, RequestHash: strings.Repeat("a", 64), BusinessDigest: strings.Repeat("b", 64), Charges: []biz.Charge{{ID: uuid.NewString(), Code: "gpu.shared_memory_mib", Units: 6144}}, Spec: biz.Spec{Name: "workspace", ImageID: uuid.NewString(), ImageScope: "platform", ImageReference: "registry.invalid/platform/workspace@sha256:" + strings.Repeat("c", 64), ImageDigest: "sha256:" + strings.Repeat("c", 64), PresetID: "shared", PresetVersion: 1, CPU: "1", Memory: "2Gi", UID: 1000, GID: 1000, Shell: "/bin/bash", ServiceAccount: "workload", StorageClass: "ani-block", WorkspaceSize: "1Gi", ClusterID: uuid.NewString(), Namespace: "tenant-" + tenant, NotebookName: "instance-" + id, PVCName: "workspace-" + id, GPU: biz.GPU{Scheduler: "volcano", Queue: "default", Recipe: "volcano-hami-v1", Digest: strings.Repeat("d", 64), NodeLabels: map[string]string{"accelerator.ani.io/supply-group": "test"}, Annotations: map[string]string{"volcano.sh/vgpu-mode": "hami-core"}, Limits: map[string]string{"volcano.sh/vgpu-number": "1", "volcano.sh/vgpu-memory": "6", "volcano.sh/vgpu-cores": "25"}, Plan: json.RawMessage(`{"frozen_external_plan":true}`)}}}
}

// The only substitutes are the external Kubernetes and Gov boundaries. All
// admission, versions, pending actions, leasing and cleanup progress use PG.
type provider struct {
	mu               sync.Mutex
	obs              biz.Observation
	unknown          bool
	failPVC          bool
	failReclaim      bool
	failUpdate       bool
	creates, updates int
}

func (p *provider) Observe(context.Context, biz.Instance) (biz.Observation, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.obs, nil
}
func (p *provider) EnsureWorkspace(context.Context, biz.Instance) (string, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.obs.PVCExists = true
	p.obs.PVCUID = "pvc-original"
	p.obs.VolumeExists = true
	p.obs.VolumeName = "volume-original"
	p.obs.VolumeUID = "volume-original-uid"
	return p.obs.PVCUID, nil
}
func (p *provider) CreateNotebook(_ context.Context, i biz.Instance) (string, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.creates++
	if p.unknown {
		return "", biz.ErrUnknown
	}
	p.obs.NotebookExists = true
	p.obs.NotebookUID = "notebook-original"
	p.obs.NotebookRV = "1"
	p.obs.IntentVersion = i.Version
	p.obs.PodExists = true
	p.obs.PodReady = true
	p.obs.PodUID = "pod-1"
	p.obs.PodName = "notebook-0"
	return p.obs.NotebookUID, nil
}
func (p *provider) SetStopped(_ context.Context, i biz.Instance, _ biz.Observation, stop bool) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.failUpdate {
		return errors.New("external conditional update rejected")
	}
	p.updates++
	p.obs.Stopped = stop
	p.obs.IntentVersion = i.Version
	p.obs.PodExists = !stop
	p.obs.PodReady = !stop
	if !stop {
		p.obs.PodUID = "pod-restarted"
	}
	return nil
}
func (p *provider) DeleteNotebook(context.Context, biz.Instance, biz.Observation) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.obs.NotebookExists = false
	p.obs.PodExists = false
	p.obs.PodReady = false
	return nil
}
func (p *provider) DeleteWorkspace(context.Context, biz.Instance, biz.Observation) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.failPVC {
		return errors.New("external PVC delete forbidden")
	}
	p.obs.PVCExists = false
	if !p.failReclaim {
		p.obs.VolumeExists = false
	}
	return nil
}

type quota struct {
	calls   int
	origins []string
}

func (q *quota) Report(_ context.Context, i biz.Instance) error {
	if !i.ComputeCleaned || i.Pending != nil {
		return errors.New("release before durable cleanup")
	}
	q.calls++
	q.origins = append(q.origins, i.CreateOperationID)
	return nil
}
func step(t *testing.T, f *fixture, w *biz.Worker, c biz.Create) biz.Instance {
	t.Helper()
	ctx := context.Background()
	if _, e := f.runtime.Exec(ctx, "UPDATE instance.instance_work SET due_at=clock_timestamp() WHERE tenant_id=$1 AND instance_id=$2", c.TenantID, c.ID); e != nil {
		t.Fatal(e)
	}
	if _, e := w.Step(ctx); e != nil {
		t.Fatal(e)
	}
	i, e := f.repo.Get(ctx, c.TenantID, c.ID)
	if e != nil {
		t.Fatal(e)
	}
	return i
}
func command(t *testing.T, f *fixture, c biz.Create, i biz.Instance, action string) biz.Command {
	t.Helper()
	r := biz.Command{TenantID: c.TenantID, ID: c.ID, OperationID: uuid.NewString(), Key: uuid.NewString(), RequestHash: strings.Repeat("e", 64), Action: action, ExpectedVersion: i.Version}
	a, e := f.repo.AcceptCommand(context.Background(), r)
	if e != nil || !a.Accepted {
		t.Fatal(a, e)
	}
	return r
}
func TestInstancePersistentLifecycle(t *testing.T) {
	f := database(t)
	ctx := context.Background()
	c := create()
	a, e := f.repo.AcceptCreate(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	replay, e := f.repo.AcceptCreate(ctx, c)
	if e != nil || replay != a {
		t.Fatal("non-durable ACK replay", replay, e)
	}
	changed := c
	changed.BusinessDigest = strings.Repeat("f", 64)
	if _, e = f.repo.AcceptCreate(ctx, changed); !errors.Is(e, biz.ErrConflict) {
		t.Fatal("changed create accepted", e)
	}
	p := &provider{}
	q := &quota{}
	w, _ := biz.NewWorker(f.repo, p, q, "worker-1")
	var i biz.Instance
	for n := 0; n < 3; n++ {
		i = step(t, f, w, c)
	}
	if i.Observed != biz.Running {
		t.Fatal("not running", i.Reason)
	}
	for n := 0; n < 2; n++ {
		stop := command(t, f, c, i, "stop")
		i = step(t, f, w, c)
		i = step(t, f, w, c)
		if i.Observed != biz.Stopped || i.PVCUID != "pvc-original" || q.calls != 0 {
			t.Fatal("stop changed quota/workspace")
		}
		// Same-value Stop must advance the durable intent even if projection is
		// temporarily rejected. Its previous observed state cannot complete it.
		if n == 0 {
			repeated := command(t, f, c, i, "stop")
			p.failUpdate = true
			i = step(t, f, w, c)
			op, e := f.repo.GetOperation(ctx, c.TenantID, repeated.OperationID)
			if e != nil || op.State == "completed" || i.Reason != "PROVIDER_MUTATION_FAILED" {
				t.Fatal("old observation completed rejected new Stop", e)
			}
			p.failUpdate = false
			i = step(t, f, w, c)
			i = step(t, f, w, c)
			if i.Version != repeated.ExpectedVersion+1 || p.obs.IntentVersion != i.Version {
				t.Fatal("same-value Stop lost intent version")
			}
		}
		same, e := f.repo.AcceptCommand(ctx, stop)
		if e != nil || same.OperationID != stop.OperationID {
			t.Fatal("stop replay", e)
		}
		command(t, f, c, i, "start")
		i = step(t, f, w, c)
		i = step(t, f, w, c)
		if i.CreateOperationID != c.OperationID || !samePlan(i, c) || i.Observed != biz.Running || p.creates != 1 {
			t.Fatal("start changed original freeze")
		}
	}
	command(t, f, c, i, "delete")
	p.obs.WorkspaceReason = "WORKSPACE_OBSERVATION_FAILED"
	for n := 0; n < 5; n++ {
		i = step(t, f, w, c)
	}
	if !i.ComputeCleaned || !i.QuotaReturned || i.WorkspaceCleaned || q.calls != 1 || i.Reason != "WORKSPACE_OBSERVATION_FAILED" {
		t.Fatal("workspace observation failure blocked independent GPU refund", i.Reason, q.calls)
	}
	p.obs.WorkspaceReason = ""
	p.failPVC = true
	i = step(t, f, w, c)
	if !i.ComputeCleaned || !i.QuotaReturned || i.WorkspaceCleaned || i.Observed == biz.Deleted || q.calls != 1 {
		t.Fatal("PVC failure lost deletion progress", i.Reason, q.calls)
	}
	p.failPVC = false
	p.failReclaim = true
	for n := 0; n < 3; n++ {
		i = step(t, f, w, c)
	}
	if i.WorkspaceCleaned || i.Observed == biz.Deleted || !i.QuotaReturned || i.Reason != "WAITING_FOR_WORKSPACE_RECLAIM" {
		t.Fatal("PVC absence completed before volume reclaim", i.Reason)
	}
	p.obs.VolumeExists = false
	p.failReclaim = false
	for n := 0; n < 3; n++ {
		i = step(t, f, w, c)
	}
	if i.Observed != biz.Deleted || !i.WorkspaceCleaned || q.calls != 1 {
		t.Fatal("cleanup recovery", i, q.calls)
	}
	// A new instance with unknown CREATE stays pending across DELETE and worker restart.
	c2 := create()
	if _, e = f.repo.AcceptCreate(ctx, c2); e != nil {
		t.Fatal(e)
	}
	p2 := &provider{unknown: true}
	q2 := &quota{}
	w2, _ := biz.NewWorker(f.repo, p2, q2, "before-restart")
	step(t, f, w2, c2)
	i = step(t, f, w2, c2)
	command(t, f, c2, i, "delete")
	w2, _ = biz.NewWorker(f.repo, p2, q2, "after-restart")
	for n := 0; n < 3; n++ {
		i = step(t, f, w2, c2)
	}
	if i.Pending == nil || i.Reason != "CREATE_RESULT_UNKNOWN" || q2.calls != 0 || p2.creates != 1 {
		t.Fatal("unknown CREATE incorrectly closed", i.Reason)
	}
}
func samePlan(i biz.Instance, c biz.Create) bool {
	a, _ := json.Marshal(i.Spec)
	b, _ := json.Marshal(c.Spec)
	return string(a) == string(b) && len(i.Charges) == len(c.Charges) && i.Charges[0] == c.Charges[0]
}
func TestInstanceDeleteFirstAndTenant(t *testing.T) {
	f := database(t)
	ctx := context.Background()
	c := create()
	d := biz.Delete{TenantID: c.TenantID, ID: c.ID, OperationID: uuid.NewString(), CreateOperationID: c.OperationID, RequestHash: strings.Repeat("e", 64), GPU: c.Spec.GPU, Charges: c.Charges}
	a, e := f.repo.AcceptDelete(ctx, d)
	if e != nil {
		t.Fatal(e)
	}
	replay, e := f.repo.AcceptDelete(ctx, d)
	if e != nil || replay != a {
		t.Fatal("delete replay", e)
	}
	if _, e = f.repo.AcceptCreate(ctx, c); e != nil {
		t.Fatal("late create ACK", e)
	}
	i, e := f.repo.Get(ctx, c.TenantID, c.ID)
	if e != nil || i.Desired != biz.Deleted {
		t.Fatal("late create reopened tombstone", e)
	}
	p := &provider{}
	q := &quota{}
	w, _ := biz.NewWorker(f.repo, p, q, "delete-first")
	for n := 0; n < 3; n++ {
		i = step(t, f, w, c)
	}
	if p.creates != 0 || q.calls != 1 || i.Observed != biz.Deleted {
		t.Fatal("delete first created resources")
	}
	if _, e = f.repo.Get(ctx, uuid.NewString(), c.ID); !errors.Is(e, biz.ErrNotFound) {
		t.Fatal("cross tenant instance leak", e)
	}
	if _, e = f.repo.GetOperation(ctx, uuid.NewString(), d.OperationID); !errors.Is(e, biz.ErrNotFound) {
		t.Fatal("cross tenant operation leak", e)
	}
	if e = biz.RequireTenant(ctx, c.TenantID); !errors.Is(e, biz.ErrDenied) {
		t.Fatal("missing trusted principal accepted")
	}
}

// This substitutes only the remote Gov boundary. The production Resource
// outbox, worker and terminal authority all share the restricted real PG repo.
type releaseEndpoint struct {
	request        *quotapb.ReportQuotaReleaseRequest
	calls, applied int
}

func (q *releaseEndpoint) ReportQuotaRelease(_ context.Context, r *quotapb.ReportQuotaReleaseRequest, _ ...grpc.CallOption) (*quotapb.ReportQuotaReleaseResponse, error) {
	q.calls++
	if q.request == nil {
		q.request = proto.Clone(r).(*quotapb.ReportQuotaReleaseRequest)
		q.applied++
		return nil, status.Error(codes.Unavailable, "response lost after commit")
	}
	if !proto.Equal(q.request, r) {
		return nil, errors.New("release retry changed original event")
	}
	reply := new(quotapb.ReportQuotaReleaseResponse)
	for _, c := range r.Items {
		reply.Items = append(reply.Items, &quotapb.QuotaReleaseResult{ChargeId: c.ChargeId, ReleasedTotal: c.ReleasedTotal})
	}
	return reply, nil
}
func TestInstanceReleaseRetryAndTerminalRevocation(t *testing.T) {
	f := database(t)
	ctx := context.Background()
	c := create()
	if _, e := f.repo.AcceptCreate(ctx, c); e != nil {
		t.Fatal(e)
	}
	p := &provider{}
	remote := &releaseEndpoint{}
	reporter, e := data.NewQuota(f.repo, remote)
	if e != nil {
		t.Fatal(e)
	}
	worker, _ := biz.NewWorker(f.repo, p, reporter, "initial-worker")
	var i biz.Instance
	for n := 0; n < 3; n++ {
		i = step(t, f, worker, c)
	}
	authority := service.NewTerminalValidation(f.repo, p)
	target := func(i biz.Instance) *terminal.TerminalTarget {
		return &terminal.TerminalTarget{TenantId: i.TenantID, InstanceId: i.ID, Version: i.Version, ClusterId: i.Spec.ClusterID, Namespace: i.Spec.Namespace, NotebookName: i.Spec.NotebookName, NotebookUid: i.NotebookUID, PodName: i.PodName, PodUid: i.PodUID, ContainerName: biz.Container, Shell: i.Spec.Shell}
	}
	old := target(i)
	if _, e = authority.ValidateTerminalTarget(ctx, old); e != nil {
		t.Fatal("running target rejected", e)
	}
	if e = reporter.Report(ctx, i); !errors.Is(e, biz.ErrInvalid) {
		t.Fatal("release permitted before cleanup", e)
	}
	command(t, f, c, i, "stop")
	if _, e = authority.ValidateTerminalTarget(ctx, old); status.Code(e) != codes.FailedPrecondition {
		t.Fatal("new stop intent did not revoke terminal", e)
	}
	for n := 0; n < 2; n++ {
		i = step(t, f, worker, c)
	}
	command(t, f, c, i, "start")
	for n := 0; n < 2; n++ {
		i = step(t, f, worker, c)
	}
	if _, e = authority.ValidateTerminalTarget(ctx, old); status.Code(e) != codes.FailedPrecondition {
		t.Fatal("old ticket targeted restarted Pod", e)
	}
	if _, e = authority.ValidateTerminalTarget(ctx, target(i)); e != nil {
		t.Fatal("new running target rejected", e)
	}
	command(t, f, c, i, "delete")
	p.failPVC = true
	for n := 0; n < 6 && remote.calls == 0; n++ {
		i = step(t, f, worker, c)
	}
	if remote.calls != 1 || i.QuotaReturned || !i.ComputeCleaned || i.WorkspaceCleaned {
		t.Fatal("lost receipt changed cleanup evidence")
	}
	reporter, e = data.NewQuota(f.repo, remote)
	if e != nil {
		t.Fatal(e)
	}
	worker, _ = biz.NewWorker(f.repo, p, reporter, "recovered-worker")
	for n := 0; n < 3; n++ {
		i = step(t, f, worker, c)
	}
	if remote.calls != 2 || remote.applied != 1 || !i.QuotaReturned || i.WorkspaceCleaned || i.Observed == biz.Deleted {
		t.Fatal("durable release retry/partial delete failed")
	}
	if remote.request.OperationId != c.OperationID || len(remote.request.Items) != len(c.Charges) || remote.request.Items[0].ChargeId != c.Charges[0].ID {
		t.Fatal("release lost original ledger binding")
	}
	p.failPVC = false
	for n := 0; n < 3; n++ {
		i = step(t, f, worker, c)
	}
	if i.Observed != biz.Deleted || remote.calls != 2 {
		t.Fatal("final cleanup retried settled quota")
	}
}

func TestInstanceSupersededMutationAndLeaseFencing(t *testing.T) {
	f := database(t)
	ctx := context.Background()
	c := create()
	if _, e := f.repo.AcceptCreate(ctx, c); e != nil {
		t.Fatal(e)
	}
	work, e := f.repo.Claim(ctx, "original-worker", time.Minute)
	if e != nil || work == nil {
		t.Fatal("claim", e)
	}
	mutation := biz.Mutation{Kind: "create_notebook", OperationID: c.OperationID, Version: work.Instance.Version}
	if e = f.repo.BeginMutation(ctx, *work, mutation); e != nil {
		t.Fatal(e)
	}
	stop := command(t, f, c, work.Instance, "stop")
	result := work.Instance
	result.NotebookUID = "notebook-created-before-new-stop"
	result.Observed = biz.Running
	if e = f.repo.Finish(ctx, *work, biz.Progress{Instance: result, Receipt: &biz.MutationReceipt{Mutation: mutation, Outcome: "succeeded"}, Next: time.Millisecond}); e != nil {
		t.Fatal("attributable late result lost", e)
	}
	current, e := f.repo.Get(ctx, c.TenantID, c.ID)
	if e != nil || current.Desired != biz.Stopped || current.Version != 2 || current.LastOperationID != stop.OperationID || current.Observed == biz.Running || current.Pending != nil || current.NotebookUID != result.NotebookUID {
		t.Fatal("late result overwrote current intent", e)
	}
	var receipt []byte
	if e = f.runtime.QueryRow(ctx, "SELECT external_result FROM instance.instance_operations WHERE tenant_id=$1 AND operation_id=$2", c.TenantID, c.OperationID).Scan(&receipt); e != nil || !strings.Contains(string(receipt), "succeeded") {
		t.Fatal("original operation lost external receipt", e)
	}
	if _, e = f.runtime.Exec(ctx, "UPDATE instance.instance_work SET due_at=clock_timestamp() WHERE tenant_id=$1 AND instance_id=$2", c.TenantID, c.ID); e != nil {
		t.Fatal(e)
	}
	old, e := f.repo.Claim(ctx, "expiring-worker", time.Minute)
	if e != nil || old == nil {
		t.Fatal(e)
	}
	command(t, f, c, old.Instance, "start")
	if e = f.repo.BeginMutation(ctx, *old, biz.Mutation{Kind: "set_stopped", OperationID: old.Instance.LastOperationID, Version: old.Instance.Version}); !errors.Is(e, biz.ErrVersion) {
		t.Fatal("old mutation crossed intent fence", e)
	}
	// Only the lease clock is advanced in this failure fixture; business state
	// is changed exclusively by admission and Finish.
	if _, e = f.runtime.Exec(ctx, "UPDATE instance.instance_work SET lease_until='-infinity',due_at=clock_timestamp() WHERE tenant_id=$1 AND instance_id=$2", c.TenantID, c.ID); e != nil {
		t.Fatal(e)
	}
	newer, e := f.repo.Claim(ctx, "replacement-worker", time.Minute)
	if e != nil || newer == nil || newer.Epoch <= old.Epoch {
		t.Fatal("lease takeover", e)
	}
	if e = f.repo.Finish(ctx, *old, biz.Progress{Instance: old.Instance, Next: time.Millisecond}); !errors.Is(e, biz.ErrLease) {
		t.Fatal("expired worker overwrote current state", e)
	}
	current, e = f.repo.Get(ctx, c.TenantID, c.ID)
	if e != nil || current.Desired != biz.Running || current.Version != 3 {
		t.Fatal("lease fence lost newest intent", e)
	}
}
