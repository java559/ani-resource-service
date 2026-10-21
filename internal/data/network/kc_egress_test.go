package data

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network/sqlcgen"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
	"k8s.io/client-go/tools/cache"
)

func crFixture(kind, ns, name string) *unstructured.Unstructured {
	o := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "networking.kubercloud.com/v1", "kind": kind, "metadata": map[string]any{"name": name, "namespace": ns, "uid": "uid-" + name, "resourceVersion": "10", "generation": int64(2)}, "spec": map[string]any{}, "status": map[string]any{"conditions": []any{map[string]any{"type": "Valid", "status": "True"}, map[string]any{"type": "Initialized", "status": "True"}, map[string]any{"type": "Ready", "status": "True"}}}}}
	return o
}
func poolFixtures() (egressResolved, *unstructured.Unstructured, *unstructured.Unstructured, *unstructured.Unstructured) {
	r := egressResolved{pool: sqlcgen.NetworkPublicPool{Mode: "overlay", Cidr: "192.0.2.0/24", OvnGatewayIp: "192.0.2.1", ExcludedIps: []string{"192.0.2.1", "192.0.2.8..192.0.2.10"}}}
	pool := crFixture("Subnet", kcSystemNamespace, "pool")
	gw := crFixture("EIPGateway", "", "gateway")
	gw.Object["spec"] = map[string]any{"scope": "Public", "egressType": "Host"}
	_ = unstructured.SetNestedField(gw.Object, "100.64.0.2", "status", "localIP")
	_ = unstructured.SetNestedField(gw.Object, "router", "status", "boundResources", "router")
	pool.Object["spec"] = renderPublicPool(r.pool, "gateway", "")
	eip := crFixture("EIP", "tenant-a", "eip")
	eip.Object["spec"] = map[string]any{"subnet": "kcn-system/pool", "ipVersion": "IPv4", "ipAddress": "192.0.2.20"}
	_ = unstructured.SetNestedField(eip.Object, "gateway", "status", "gateway")
	return r, pool, gw, eip
}
func TestEgressOverlayUnderlayProviderFieldsAndAddressEvidence(t *testing.T) {
	r, pool, gw, eip := poolFixtures()
	if !publicPoolReady(r, pool, gw, nil) {
		t.Fatal("overlay contract not ready")
	}
	if _, ok := pool.Object["spec"].(map[string]any)["underlayConfig"]; ok {
		t.Fatal("overlay emitted underlay")
	}
	for _, ip := range []string{"192.0.2.0", "192.0.2.255", "192.0.2.1", "192.0.2.9", "192.0.3.20", "::1"} {
		copy := eip.DeepCopy()
		_ = unstructured.SetNestedField(copy.Object, ip, "spec", "ipAddress")
		if ok, _ := eipAllocated(r, copy, pool, gw, nil); ok {
			t.Fatal("invalid/reserved allocation accepted", ip)
		}
	}
	if ok, ip := eipAllocated(r, eip, pool, gw, nil); !ok || ip != "192.0.2.20" {
		t.Fatal("provider allocation ignored", ok, ip)
	}
	_ = unstructured.SetNestedField(pool.Object, map[string]any{}, "spec", "underlayConfig")
	if publicPoolReady(r, pool, gw, nil) {
		t.Fatal("empty underlay object accepted on overlay")
	}
	vlanID, upstream := "vlan_"+strings.Repeat("a", 32), "192.0.2.254"
	r.pool.Mode = "underlay"
	r.pool.VlanNetworkID = &vlanID
	r.pool.UpstreamGatewayIp = &upstream
	pool.Object["spec"] = renderPublicPool(r.pool, "gateway", "vlan-zero")
	vlan := crFixture("VlanNetwork", "", "vlan-zero")
	vlan.Object["spec"] = map[string]any{"devName": "fixture0", "vlanID": int64(0)}
	_ = unstructured.SetNestedField(vlan.Object, "kcn-system/pool", "status", "subnet")
	_ = unstructured.SetNestedField(pool.Object, map[string]any{"ready": true, "chassisNode": "node-a", "notReadyNodes": []any{}}, "status", "underlayState")
	if !publicPoolReady(r, pool, gw, vlan) {
		t.Fatal("untagged underlay contract rejected")
	}
	_ = unstructured.SetNestedField(pool.Object, []any{"node-b"}, "status", "underlayState", "notReadyNodes")
	if publicPoolReady(r, pool, gw, vlan) {
		t.Fatal("partially ready underlay accepted")
	}
}
func TestEgressBindingRequiresExactNamespaceGenerationAndAppliedState(t *testing.T) {
	_, _, _, eip := poolFixtures()
	snat := crFixture("Snat", "tenant-a", "binding")
	snat.Object["spec"] = map[string]any{"eip": "eip", "vpc": "vpc", "disable": false}
	_ = unstructured.SetNestedField(snat.Object, "Bound", "status", "phase")
	_ = unstructured.SetNestedField(snat.Object, "192.0.2.20", "status", "eipAddress")
	vpc := crFixture("VPC", "tenant-a", "vpc")
	_ = unstructured.SetNestedField(vpc.Object, int64(2), "status", "observedGeneration")
	_ = unstructured.SetNestedField(eip.Object, "Bound", "status", "phase")
	bound := map[string]any{"resourceType": "Snat", "resource": "binding", "vpc": "tenant-a/vpc", "nodeName": "worker-2", "observedGeneration": int64(2), "disabled": false}
	_ = unstructured.SetNestedMap(eip.Object, bound, "status", "boundResource")
	if !bindingApplied(eip, snat, vpc, true) {
		t.Fatal("valid application evidence rejected")
	}
	for _, tc := range []struct {
		name   string
		mutate func(*unstructured.Unstructured, *unstructured.Unstructured, *unstructured.Unstructured)
	}{
		{"namespace", func(e, s, v *unstructured.Unstructured) { s.SetNamespace("tenant-b") }},
		{"vpc_namespace", func(e, s, v *unstructured.Unstructured) { v.SetNamespace("tenant-b") }},
		{"generation", func(e, s, v *unstructured.Unstructured) { s.SetGeneration(3) }},
		{"bound_name", func(e, s, v *unstructured.Unstructured) {
			_ = unstructured.SetNestedField(e.Object, "other", "status", "boundResource", "resource")
		}},
		{"egress_node", func(e, s, v *unstructured.Unstructured) {
			_ = unstructured.SetNestedField(e.Object, "", "status", "boundResource", "nodeName")
		}},
		{"disable", func(e, s, v *unstructured.Unstructured) {
			_ = unstructured.SetNestedField(s.Object, true, "spec", "disable")
		}},
		{"applied_disable", func(e, s, v *unstructured.Unstructured) {
			_ = unstructured.SetNestedField(e.Object, true, "status", "boundResource", "disabled")
		}},
		{"vpc_generation", func(e, s, v *unstructured.Unstructured) {
			_ = unstructured.SetNestedField(v.Object, int64(1), "status", "observedGeneration")
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e, s, v := eip.DeepCopy(), snat.DeepCopy(), vpc.DeepCopy()
			tc.mutate(e, s, v)
			if bindingApplied(e, s, v, true) {
				t.Fatal("stale/mismatched binding accepted")
			}
		})
	}
	_ = unstructured.SetNestedField(snat.Object, true, "spec", "disable")
	_ = unstructured.SetNestedField(snat.Object, "Disabled", "status", "phase")
	_ = unstructured.SetNestedField(eip.Object, true, "status", "boundResource", "disabled")
	_ = unstructured.SetNestedField(eip.Object, "", "status", "boundResource", "nodeName")
	if !bindingApplied(eip, snat, vpc, false) {
		t.Fatal("disabled binding incorrectly required active egress node")
	}
}
func TestEgressUIDConflictsAndNatServiceDependencyIndex(t *testing.T) {
	b := sqlcgen.NetworkProviderBinding{TenantID: "tenant-a", ResourceKind: "eip", Namespace: "tenant-a", ProviderName: "eip", ProviderUid: "uid-eip", BindingID: "binding-identity"}
	o := crFixture("EIP", "tenant-a", "eip")
	o.SetLabels(map[string]string{ownerLabel: "ani-network-service", resourceLabel: "resource-id", tenantLabel: "tenant-a", bindingLabel: b.BindingID})
	if err := inspectEgressIdentity(o, b, "resource-id", b.ProviderUid); err != nil {
		t.Fatal(err)
	}
	o.SetUID("replacement")
	if inspectEgressIdentity(o, b, "resource-id", b.ProviderUid) == nil {
		t.Fatal("same name recreated object adopted")
	}
	nat := crFixture("Nat", "tenant-a", "nat")
	nat.Object["spec"] = map[string]any{"eip": "eip"}
	set := egressReadSet{all: map[schema.GroupVersionResource][]unstructured.Unstructured{kcNats: {*nat}}}
	if !eipBindingConflict(set, "tenant-a", "eip", "") {
		t.Fatal("Nat dependency ignored")
	}
	if eipBindingConflict(set, "tenant-b", "eip", "") {
		t.Fatal("cross namespace same name matched")
	}
	svc := crFixture("Service", "tenant-a", "loadbalancer")
	svc.Object["spec"] = map[string]any{"type": "LoadBalancer"}
	svc.SetAnnotations(map[string]string{"networking.kubercloud.com/lb_eips": " eip,second "})
	set.all = map[schema.GroupVersionResource][]unstructured.Unstructured{kcServices: {*svc}}
	if !eipBindingConflict(set, "tenant-a", "eip", "") {
		t.Fatal("Service dependency ignored")
	}
	observer := &KCObservation{options: ObservationOptions{QueueCapacity: 32}, pending: map[string]struct{}{}, changes: map[string]uint64{}}
	next := nat.DeepCopy()
	next.Object["spec"] = map[string]any{"eip": "new-eip"}
	observer.changed(nat, next)
	observer.changed(cache.DeletedFinalStateUnknown{Key: "tenant-a/nat", Obj: next}, nil)
	for _, key := range []string{"eip:tenant-a/eip", "eip:tenant-a/new-eip", "uid:uid-nat"} {
		if _, ok := observer.pending[key]; !ok {
			t.Fatal("lost relation/tombstone", key)
		}
	}
}
func TestEgressProviderImagesUseRunningDigests(t *testing.T) {
	objects := []unstructured.Unstructured{}
	digest := "sha256:" + strings.Repeat("b", 64)
	for _, role := range []string{"controller", "cni-ds", "ovs-ds", "ovn-central"} {
		o := crFixture("Pod", kcSystemNamespace, role)
		o.SetLabels(map[string]string{"networking.kubercloud.com/app": role})
		o.Object["spec"] = map[string]any{"containers": []any{map[string]any{"name": role}}}
		o.Object["status"] = map[string]any{"phase": "Running", "containerStatuses": []any{map[string]any{"name": role, "ready": true, "imageID": "docker-pullable://provider@" + digest}}}
		objects = append(objects, *o)
	}
	if got := providerImages(objects); len(got) != 1 || got[0] != digest {
		t.Fatal("image digest evidence", got)
	}
	unstructured.RemoveNestedField(objects[0].Object, "status", "containerStatuses")
	if len(providerImages(objects)) != 0 {
		t.Fatal("missing running digest preserved acceptance evidence")
	}
}
func TestNodeFactsCollectorUsesOnlyReadonlyCommandsAndFencesPublication(t *testing.T) {
	node := crFixture("Node", "", "node-a")
	node.SetAPIVersion("v1")
	node.SetUID("node-uid")
	cm := crFixture("ConfigMap", kcSystemNamespace, nodeFactsName("node-uid"))
	cm.SetAPIVersion("v1")
	cm.SetLabels(map[string]string{ownerLabel: factsOwner, factsNodeLabel: "node-uid"})
	client := fake.NewSimpleDynamicClient(runtime.NewScheme(), node, cm)
	c := newNodeFactsCollector(client, "node-a", "node-uid")
	now := time.Now()
	c.Now = func() time.Time { return now }
	c.Physical = func(path string) bool { return strings.Contains(path, "eth") }
	c.ReadFile = func(string) ([]byte, error) { return []byte("1\n"), nil }
	commands := []string{}
	c.Run = func(_ context.Context, name string, args ...string) ([]byte, error) {
		cmd := name + " " + strings.Join(args, " ")
		commands = append(commands, cmd)
		switch cmd {
		case "ip -j -d link show":
			return []byte(`[{"ifname":"eth0","ifindex":2,"mtu":1500,"flags":["UP"]},{"ifname":"eth1","ifindex":3,"mtu":1500,"flags":["UP"]},{"ifname":"veth0","ifindex":4,"linkinfo":{"info_kind":"veth"}}]`), nil
		case "ip -j address show":
			return []byte(`[{"ifname":"eth0","addr_info":[{"local":"10.0.0.2","prefixlen":24}]}]`), nil
		case "ip -j route show table all":
			return []byte(`[{"dst":"default","dev":"eth0"}]`), nil
		case "ip -6 -j route show table all":
			return []byte(`[]`), nil
		case "ovs-vsctl --timeout=3 --format=json --columns=name list Interface":
			return []byte(`{"headings":["name"],"data":[["veth0"]]}`), nil
		case "ovs-vsctl --timeout=3 --format=json --columns=external_ids list Open_vSwitch .":
			return []byte(`{"headings":["external_ids"],"data":[[["map",[]]]]}`), nil
		default:
			return nil, fmt.Errorf("unexpected command %s", cmd)
		}
	}
	doc, err := c.Collect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	filtered := biz.FilterNodeInterfaces(doc.Interfaces, now, time.Minute)
	if len(commands) != 6 || !doc.OVSBridgeMappingsObserved || len(filtered) != 3 || filtered[0].Selectable || !filtered[1].Selectable || filtered[2].Selectable {
		t.Fatal("unsafe link inventory", filtered, commands)
	}
	if err = c.Publish(context.Background(), doc); err != nil {
		t.Fatal(err)
	}
	got, err := client.Resource(kcConfigMaps).Namespace(kcSystemNamespace).Get(context.Background(), cm.GetName(), metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	var saved NodeFactsDocument
	if json.Unmarshal([]byte(crString(got, "data", "facts.json")), &saved) != nil || !saved.CollectedAt.Equal(now) {
		t.Fatal("raw collection timestamp changed")
	}
	c.ConfigMapUID = "other-uid"
	if c.Publish(context.Background(), doc) == nil {
		t.Fatal("collector replaced another facts object")
	}
	c.Run = func(context.Context, string, ...string) ([]byte, error) { return nil, fmt.Errorf("probe offline") }
	if _, err = c.Collect(context.Background()); err == nil {
		t.Fatal("offline probe fabricated fresh facts")
	}
}
