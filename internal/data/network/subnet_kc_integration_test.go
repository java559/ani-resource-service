package data_test

import (
	"context"
	"errors"
	networkv1 "github.com/zhangzhe-ctrl/ani-resource-service/api/network/v1"
	service "github.com/zhangzhe-ctrl/ani-resource-service/internal/service/network"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/tests/testenv"
	"k8s.io/client-go/rest"
)

func TestSubnetKCContractAndResidualReferencesBlockCleanup(t *testing.T) {
	p, _ := database(t)
	n := newNetwork(t, p, time.Minute)
	api := &testenv.KC{}
	host := httptest.NewServer(api)
	defer host.Close()
	provider, err := data.NewKCProvider(p, &rest.Config{Host: host.URL, QPS: 1000, Burst: 1000})
	if err != nil {
		t.Fatal(err)
	}
	policy := biz.DefaultWorkerPolicy()
	policy.ObserveEvery = 100 * time.Millisecond
	policy.RetryMin = time.Millisecond
	w, err := biz.NewWorker(p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	tenant := uuid.NewString()
	v := availableVPC(t, n, w, tenant, "parent")
	ctx := context.Background()
	accepted, err := n.CreateSubnet(ctx, biz.CreateSubnet{TenantID: tenant, VPCID: v.ID, Name: "http", CIDR: "10.42.1.0/24", IdempotencyKey: "http"})
	if err != nil {
		t.Fatal(err)
	}
	current := driveSubnet(t, n, w, accepted, biz.Available)
	if current.Gateway != "10.42.1.1" {
		t.Fatal("wrong gateway", current)
	}
	api.Mu.Lock()
	var object map[string]any
	var key string
	for k, o := range api.Objects {
		if o["kind"] == "Subnet" {
			key, object = k, o
		}
	}
	metadata := object["metadata"].(map[string]any)
	namespace := metadata["namespace"].(string)
	providerName := metadata["name"].(string)
	spec := object["spec"].(map[string]any)
	if spec["gatewayIP"] != "10.42.1.1" || spec["type"] != "VPC" {
		t.Fatal("incorrect serialized subnet", spec)
	}
	api.Mu.Unlock()
	if _, err := n.DeleteSubnet(ctx, tenant, current.ID); err != nil {
		t.Fatal(err)
	}
	// A disappeared CR is insufficient while a Provider IP still points at it.
	api.Mu.Lock()
	delete(api.Objects, key)
	api.Objects["vnicips/"+namespace+"/residual"] = map[string]any{"apiVersion": "networking.kubercloud.com/v1", "kind": "VNicIP", "metadata": map[string]any{"name": "residual", "namespace": namespace}, "spec": map[string]any{"subnet": providerName}}
	api.Mu.Unlock()
	for i := 0; i < 4; i++ {
		if _, err := w.Step(ctx); err != nil {
			t.Fatal(err)
		}
	}
	pending, err := n.GetSubnet(ctx, tenant, current.ID)
	if err != nil || pending.State != biz.Deleting || pending.Reason != biz.ResourceInUse {
		t.Fatalf("residual IP ignored: %+v %v", pending, err)
	}
	api.Mu.Lock()
	delete(api.Objects, "vnicips/"+namespace+"/residual")
	api.Objects[key] = object
	api.Mu.Unlock()
	driveSubnet(t, n, w, current, biz.Deleted)
	api.Mu.Lock()
	defer api.Mu.Unlock()
	if api.Creates["subnets"] != 1 || api.Deletes["subnets"] != 1 || api.Invalid != "" {
		t.Fatalf("provider calls: create=%d delete=%d invalid=%s", api.Creates["subnets"], api.Deletes["subnets"], api.Invalid)
	}
}

func TestSubnetKCIPCountsDetailsAndFences(t *testing.T) {
	p, owner := database(t)
	n := newNetwork(t, p, time.Minute)
	api := &testenv.KC{}
	var unavailable atomic.Bool
	host := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if unavailable.Load() && strings.Contains(r.URL.Path, "/subnets/") {
			w.WriteHeader(503)
			return
		}
		api.ServeHTTP(w, r)
	}))
	defer host.Close()
	provider, err := data.NewKCProvider(p, &rest.Config{Host: host.URL, QPS: 1000, Burst: 1000})
	if err != nil {
		t.Fatal(err)
	}
	policy := biz.DefaultWorkerPolicy()
	policy.ObserveEvery = time.Hour
	policy.StaleAfter = 2 * time.Hour
	policy.RetryMin = time.Millisecond
	worker, err := biz.NewWorker(p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	tenant := uuid.NewString()
	vpc := availableVPC(t, n, worker, tenant, "counts-parent")
	accepted, err := n.CreateSubnet(ctx, biz.CreateSubnet{TenantID: tenant, VPCID: vpc.ID, Name: "counts", CIDR: "10.42.1.0/24", IdempotencyKey: "counts"})
	if err != nil {
		t.Fatal(err)
	}
	current := driveSubnet(t, n, worker, accepted, biz.Available)
	rpc := service.NewNetworkService(n)
	details := func() *networkv1.Subnet {
		t.Helper()
		reply, e := rpc.GetSubnet(ctx, &networkv1.GetSubnetRequest{TenantId: tenant, SubnetId: current.ID})
		if e != nil {
			t.Fatal(e)
		}
		return reply.Subnet
	}
	if got := details(); got.AvailableIpCount != nil || got.UsedIpCount != nil {
		t.Fatal("missing status invented counts", got)
	}
	var object map[string]any
	api.Mu.Lock()
	for _, o := range api.Objects {
		if o["kind"] == "Subnet" {
			object = o
		}
	}
	api.Mu.Unlock()
	metadata := object["metadata"].(map[string]any)
	labels := metadata["labels"].(map[string]any)
	statusFields := object["status"].(map[string]any)
	refresh := func(change func()) {
		t.Helper()
		api.Mu.Lock()
		change()
		api.Mu.Unlock()
		if e := p.NotifyResource(ctx, tenant, current.ID); e != nil {
			t.Fatal(e)
		}
		if _, e := worker.Step(ctx); e != nil {
			t.Fatal(e)
		}
	}
	refresh(func() { statusFields["v4availableIPs"] = int64(240); statusFields["v4usingIPs"] = int64(12) })
	before := details()
	if before.AvailableIpCount == nil || before.GetAvailableIpCount() != 240 || before.UsedIpCount == nil || before.GetUsedIpCount() != 12 {
		t.Fatal("counts not persisted to detail", before)
	}
	refresh(func() { statusFields["v4availableIPs"] = int64(239); statusFields["v4usingIPs"] = int64(13) })
	if got := details(); got.GetAvailableIpCount() != 239 || got.GetUsedIpCount() != 13 || got.State != before.State || !got.ObservedAt.AsTime().After(before.ObservedAt.AsTime()) {
		t.Fatal("count-only refresh lost", got)
	}
	refresh(func() { statusFields["v4availableIPs"] = int64(0); statusFields["v4usingIPs"] = int64(0) })
	if got := details(); got.AvailableIpCount == nil || got.UsedIpCount == nil || got.GetAvailableIpCount() != 0 || got.GetUsedIpCount() != 0 {
		t.Fatal("zero lost presence", got)
	}
	refresh(func() { delete(statusFields, "v4availableIPs"); statusFields["v4usingIPs"] = "invalid" })
	if got := details(); got.AvailableIpCount != nil || got.UsedIpCount != nil {
		t.Fatal("new missing/invalid counts retained old values", got)
	}
	refresh(func() { statusFields["v4availableIPs"] = int64(-1); statusFields["v4usingIPs"] = 1.5 })
	if got := details(); got.AvailableIpCount != nil || got.UsedIpCount != nil {
		t.Fatal("invalid numeric counts accepted", got)
	}
	refresh(func() { statusFields["v4availableIPs"] = int64(200); statusFields["v4usingIPs"] = int64(20) })
	before = details()
	oldUID := metadata["uid"]
	refresh(func() { metadata["uid"] = uuid.NewString(); statusFields["v4availableIPs"] = int64(999) })
	if got := details(); got.GetAvailableIpCount() != 200 || !got.ObservedAt.AsTime().Equal(before.ObservedAt.AsTime()) {
		t.Fatal("foreign UID renewed counts", got)
	}
	time.Sleep(policy.RetryMin)
	refresh(func() { metadata["uid"] = oldUID; labels["network.ani.io/tenant-id"] = uuid.NewString() })
	if got := details(); got.GetAvailableIpCount() != 200 || !got.ObservedAt.AsTime().Equal(before.ObservedAt.AsTime()) {
		t.Fatal("foreign tenant renewed counts", got)
	}
	time.Sleep(policy.RetryMin)
	refresh(func() { labels["network.ani.io/tenant-id"] = tenant; statusFields["observedGeneration"] = int64(0) })
	if got := details(); got.AvailableIpCount != nil || got.UsedIpCount != nil {
		t.Fatal("unobserved generation trusted", got)
	}
	time.Sleep(policy.RetryMin)
	refresh(func() { statusFields["observedGeneration"] = int64(1); statusFields["v4availableIPs"] = int64(200) })
	before = details()
	unavailable.Store(true)
	refresh(func() {})
	if got := details(); got.GetAvailableIpCount() != 200 || !got.ObservedAt.AsTime().Equal(before.ObservedAt.AsTime()) {
		t.Fatal("read failure refreshed counts", got)
	}
	staleNetwork, e := biz.NewNetwork(p, []byte("0123456789abcdef0123456789abcdef"), time.Minute, func() time.Time { return time.Now().Add(2 * time.Minute) })
	if e != nil {
		t.Fatal(e)
	}
	stale, e := service.NewNetworkService(staleNetwork).GetSubnet(ctx, &networkv1.GetSubnetRequest{TenantId: tenant, SubnetId: current.ID})
	if e != nil || !stale.Subnet.ObservationStale || stale.Subnet.GetAvailableIpCount() != 200 {
		t.Fatal("historical counts lost stale marker", stale, e)
	}
	if _, e := rpc.GetSubnet(ctx, &networkv1.GetSubnetRequest{TenantId: uuid.NewString(), SubnetId: current.ID}); status.Code(e) != codes.NotFound {
		t.Fatal("cross-tenant detail", e)
	}
	unavailable.Store(false)
	time.Sleep(policy.RetryMin)
	if e := p.NotifyResource(ctx, tenant, current.ID); e != nil {
		t.Fatal(e)
	}
	work, found, e := p.Claim(ctx, uuid.NewString(), time.Minute)
	if e != nil || !found || work.Resource.ID != current.ID {
		t.Fatal("count fence claim", work, found, e)
	}
	zero := int64(0)
	progress := biz.Progress{State: work.Resource.State, Observed: true, Identity: work.KnownIdentity, NextDelay: time.Hour, SubnetIPCounts: biz.SubnetIPCounts{Available: &zero, Used: &zero}, Proof: biz.ObservationProof{CollectedAt: before.ObservedAt.AsTime().Add(-time.Second), CoveredGeneration: work.Requirement.RequestedGeneration}}
	if e := p.Finish(ctx, work, progress); !errors.Is(e, biz.ErrLeaseLost) {
		t.Fatal("older observation accepted", e)
	}
	progress.Proof.CollectedAt = time.Now()
	oldWork := work
	oldWork.Epoch--
	if e := p.Finish(ctx, oldWork, progress); !errors.Is(e, biz.ErrLeaseLost) {
		t.Fatal("older lease accepted", e)
	}
	if got := details(); got.GetAvailableIpCount() != 200 {
		t.Fatal("fenced completion changed counts", got)
	}
	if _, e := owner.Exec(ctx, `UPDATE network_subnets SET used_ip_count=-1 WHERE tenant_id=$1 AND subnet_id=$2`, tenant, current.ID); e == nil {
		t.Fatal("negative count bypassed SQL constraint")
	}
}
