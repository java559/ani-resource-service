//go:build networkintegration

package data_test

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	data "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
	runtime "github.com/zhangzhe-ctrl/ani-resource-service/internal/server"
	service "github.com/zhangzhe-ctrl/ani-resource-service/internal/service/network"
	controlled "github.com/zhangzhe-ctrl/ani-resource-service/tests/net05a/provider"
	jsonpatch "gopkg.in/evanphx/json-patch.v4"
)

const jointClusterUID = "b490eae7-5317-411b-8cc1-b8bf79ec1234"
const jointGroupPrefix = "vcluster.networking.kubercloud.com/"

type networkJointAggregates struct {
	steppers       []runtime.Stepper
	defaultService *service.PlatformDefaultEgressService
	identity       biz.DefaultVPCIdentity
	legacyDeviceID string
}

func wireNetworkJointAggregates(t *testing.T, f *lbAdmissionFixture, provider *data.KCProvider, policy biz.WorkerPolicy) *networkJointAggregates {
	t.Helper()
	f.f.p.UseEgressInfrastructure(provider)
	e, err := biz.NewEgress(f.f.p, provider, biz.ContextEgressAuthorization{}, []byte("0123456789abcdef0123456789abcdef"), policy.StaleAfter, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	f.f.e = e
	switchWorker, err := biz.NewVirtualSwitchWorker(f.f.p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	retirementWorker, err := biz.NewLegacyDeviceRetirementStepper(f.f.p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	vpc := f.api.Object("vpcs", "kcn-system", "kcn-cluster")
	if vpc == nil {
		t.Fatal("fixed existing default VPC required")
	}
	identity := biz.DefaultVPCIdentity{ClusterID: "test-cluster", ClusterUID: jointClusterUID, Namespace: "kcn-system", Name: "kcn-cluster", UID: fmt.Sprint(vpc["metadata"].(map[string]any)["uid"])}
	f.api.Change("namespaces", map[string]any{"apiVersion": "v1", "kind": "Namespace", "metadata": map[string]any{"name": "kube-system", "uid": jointClusterUID}}, false)
	adapter, err := data.NewDefaultEgressKCProvider(provider, identity)
	if err != nil {
		t.Fatal(err)
	}
	repo, err := data.NewDefaultVPCEgressRepository(f.f.p, identity, adapter, policy.StaleAfter)
	if err != nil {
		t.Fatal(err)
	}
	usecase, err := biz.NewDefaultVPCEgress(repo, biz.ContextEgressAuthorization{})
	if err != nil {
		t.Fatal(err)
	}
	defaultWorker, err := biz.NewDefaultVPCEgressWorker(repo, adapter, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	// This independent legacy registration has no VLAN/Pool/group reference.
	// It may be pending when HTTP closes it: the real retirement stepper retains
	// the existing lease/request drain bound, rather than inventing ready state.
	inventory, err := e.ListNodeInterfaces(f.f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	legacy, err := e.CreatePlatform(f.f.ctx, biz.PlatformIntent{Kind: "adopt_device", Name: "joint-legacy-retirement", IdempotencyKey: "joint-legacy-retirement", Device: &biz.DeviceConfig{DeviceName: "legacy0", InventoryFingerprint: inventory.Fingerprint}})
	if err != nil {
		t.Fatal(err)
	}
	return &networkJointAggregates{steppers: []runtime.Stepper{switchWorker, retirementWorker, defaultWorker}, defaultService: service.NewPlatformDefaultEgressService(usecase), identity: identity, legacyDeviceID: legacy.ID}
}

// This is exclusively the external Kubernetes/CNI side. It neither reads nor
// writes Resource SQL and never substitutes for the product worker or owner CAS.
type networkJointSwitchBoundary struct {
	mu          sync.Mutex
	ready       bool
	lastRefresh time.Time
}

func (b *networkJointSwitchBoundary) refresh(api *controlled.Server) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if time.Since(b.lastRefresh) < 500*time.Millisecond {
		return
	}
	b.publishLocked(api)
}
func jointObjects(api *controlled.Server, resource string) []map[string]any {
	api.Backend.Mu.Lock()
	defer api.Backend.Mu.Unlock()
	out := []map[string]any{}
	for key, obj := range api.Backend.Objects {
		if strings.HasPrefix(key, resource+"/") {
			body, _ := json.Marshal(obj)
			var copied map[string]any
			_ = json.Unmarshal(body, &copied)
			out = append(out, copied)
		}
	}
	return out
}
func (b *networkJointSwitchBoundary) publishLocked(api *controlled.Server) {
	node := api.Object("nodes", "", "node-1")
	cm := api.Object("configmaps", "kcn-system", "node-facts-node-1")
	if node == nil || cm == nil {
		return
	}
	var doc data.NodeFactsDocument
	if json.Unmarshal([]byte(cm["data"].(map[string]any)["facts.json"].(string)), &doc) != nil {
		return
	}
	meta := node["metadata"].(map[string]any)
	labels, _ := meta["labels"].(map[string]any)
	annotations, _ := meta["annotations"].(map[string]any)
	if annotations == nil {
		annotations = map[string]any{}
	}
	before, _ := json.Marshal(annotations)
	groups := map[string]bool{}
	for key, value := range annotations {
		if strings.HasPrefix(key, jointGroupPrefix) && fmt.Sprint(value) == "phys1" {
			groups[strings.TrimPrefix(key, jointGroupPrefix)] = false
		}
	}
	vlans := jointObjects(api, "vlannetworks")
	for _, vlan := range vlans {
		spec, _ := vlan["spec"].(map[string]any)
		if spec["devName"] != "phys1" {
			continue
		}
		group, _ := spec["vClusterName"].(string)
		wired := b.ready && group != "" && labels[jointGroupPrefix+group] == "100"
		groups[group] = groups[group] || wired
		status, _ := vlan["status"].(map[string]any)
		if status == nil {
			status = map[string]any{}
		}
		beforeStatus, _ := json.Marshal(status)
		status["conditions"] = []any{map[string]any{"type": "Valid", "status": "True"}, map[string]any{"type": "Initialized", "status": "True"}}
		status["notReadyNodes"] = []any{}
		if !wired {
			status["notReadyNodes"] = []any{"node-1"}
		}
		afterStatus, _ := json.Marshal(status)
		if string(beforeStatus) != string(afterStatus) {
			vlan["status"] = status
			api.Change("vlannetworks", vlan, false)
		}
	}
	if doc.OVSBridgeMappings == nil {
		doc.OVSBridgeMappings = map[string]string{}
	}
	for network := range doc.OVSBridgeMappings {
		if strings.HasSuffix(network, ".phys1") {
			delete(doc.OVSBridgeMappings, network)
		}
	}
	wiredNetworks := []string{}
	for group, wired := range groups {
		if wired {
			annotations[jointGroupPrefix+group] = "phys1"
			doc.OVSBridgeMappings[group+".phys1"] = "br-phys1"
			wiredNetworks = append(wiredNetworks, group+".phys1")
		} else {
			delete(annotations, jointGroupPrefix+group)
		}
	}
	managed := map[string]bool{}
	if raw, ok := annotations["networking.kubercloud.com/managed_netdevs"].(string); ok {
		_ = json.Unmarshal([]byte(raw), &managed)
	}
	delete(managed, "phys1")
	if len(wiredNetworks) > 0 {
		managed["phys1"] = true
	}
	rawManaged, _ := json.Marshal(managed)
	annotations["networking.kubercloud.com/managed_netdevs"] = string(rawManaged)
	meta["annotations"] = annotations
	after, _ := json.Marshal(annotations)
	if string(before) != string(after) {
		api.Change("nodes", node, false)
	}
	doc.CollectedAt = time.Now().UTC()
	doc.OVSBridgeMappingsObserved = true
	for index := range doc.Interfaces {
		v := &doc.Interfaces[index]
		v.ObservedAt = doc.CollectedAt
		if v.Name == "phys1" {
			v.Master = ""
			v.OVSManaged = false
			v.KCManaged = false
			v.KCBridgeReady = false
			v.KCBridgeNetworks = nil
			if len(wiredNetworks) > 0 {
				v.Master = "ovs-system"
				v.OVSManaged = true
				v.KCManaged = true
				v.KCBridgeReady = true
				v.KCBridgeNetworks = wiredNetworks
			}
		}
	}
	body, _ := json.Marshal(doc)
	cm["data"].(map[string]any)["facts.json"] = string(body)
	api.Change("configmaps", cm, false)
	b.lastRefresh = time.Now()
}
func jointHTTPResponse(w http.ResponseWriter, recorder *httptest.ResponseRecorder) {
	for key, values := range recorder.Header() {
		for _, value := range values {
			w.Header().Add(key, value)
		}
	}
	w.WriteHeader(recorder.Code)
	_, _ = w.Write(recorder.Body.Bytes())
}
func (b *networkJointSwitchBoundary) http(w http.ResponseWriter, r *http.Request, api *controlled.Server) bool {
	if r.URL.Path == "/network-joint/switch-wiring" && r.Method == http.MethodPost {
		var input struct {
			Ready bool `json:"ready"`
		}
		if json.NewDecoder(r.Body).Decode(&input) != nil {
			http.Error(w, "invalid body", 400)
			return true
		}
		b.mu.Lock()
		b.ready = input.Ready
		b.publishLocked(api)
		b.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]bool{"ready": input.Ready})
		return true
	}
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	nodePatch := r.Method == http.MethodPatch && len(parts) == 4 && parts[0] == "api" && parts[2] == "nodes"
	retirementPatch := r.Method == http.MethodPatch && r.URL.Path == "/api/v1/namespaces/kcn-system/configmaps/kcn-config" && r.URL.Query().Get("fieldManager") == "ani-resource-device-retirement"
	if nodePatch || retirementPatch {
		b.mu.Lock()
		defer b.mu.Unlock()
		resource, namespace, name := "nodes", "", parts[len(parts)-1]
		if retirementPatch {
			resource, namespace, name = "configmaps", "kcn-system", "kcn-config"
		}
		object := api.Object(resource, namespace, name)
		if object == nil {
			http.Error(w, "not found", 404)
			return true
		}
		raw, err := io.ReadAll(r.Body)
		var operations []struct {
			Op, Path string
			Value    any
		}
		if err != nil || json.Unmarshal(raw, &operations) != nil || len(operations) < 2 || operations[0].Op != "test" || operations[0].Path != "/metadata/uid" || operations[1].Op != "test" || operations[1].Path != "/metadata/resourceVersion" {
			http.Error(w, "identity CAS required", 422)
			return true
		}
		patch, err := jsonpatch.DecodePatch(raw)
		if err != nil {
			http.Error(w, "invalid patch", 422)
			return true
		}
		body, _ := json.Marshal(object)
		updated, err := patch.Apply(body)
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(409)
			_, _ = w.Write([]byte(`{"apiVersion":"v1","kind":"Status","status":"Failure","reason":"Conflict","code":409}`))
			return true
		}
		_ = json.Unmarshal(updated, &object)
		api.Change(resource, object, false)
		b.publishLocked(api)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(api.Object(resource, namespace, name))
		return true
	}
	vlanMutation := len(parts) >= 4 && parts[0] == "apis" && parts[3] == "vlannetworks" && (r.Method == http.MethodPost || r.Method == http.MethodDelete)
	subnetMutation := len(parts) >= 6 && parts[0] == "apis" && parts[3] == "namespaces" && parts[5] == "subnets" && (r.Method == http.MethodPost || r.Method == http.MethodDelete)
	if !vlanMutation && !subnetMutation {
		return false
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	var before map[string]any
	if subnetMutation && r.Method == http.MethodDelete && len(parts) == 7 {
		before = api.Object("subnets", parts[4], parts[6])
	}
	recorder := httptest.NewRecorder()
	api.ServeHTTP(recorder, r)
	if recorder.Code >= 200 && recorder.Code < 300 {
		if before != nil {
			spec, _ := before["spec"].(map[string]any)
			underlay, _ := spec["underlayConfig"].(map[string]any)
			if underlay != nil {
				vlan := api.Object("vlannetworks", "", fmt.Sprint(underlay["vlanNetwork"]))
				if vlan != nil {
					status, _ := vlan["status"].(map[string]any)
					delete(status, "subnet")
					vlan["status"] = status
					api.Change("vlannetworks", vlan, false)
				}
			}
		}
		b.publishLocked(api)
		if r.Method == http.MethodPost {
			var created map[string]any
			if json.Unmarshal(recorder.Body.Bytes(), &created) == nil {
				meta, _ := created["metadata"].(map[string]any)
				resource, namespace := "vlannetworks", ""
				if subnetMutation {
					resource, namespace = "subnets", parts[4]
				}
				if updated := api.Object(resource, namespace, fmt.Sprint(meta["name"])); updated != nil {
					recorder.Body.Reset()
					_ = json.NewEncoder(recorder).Encode(updated)
				}
			}
		}
	}
	jointHTTPResponse(w, recorder)
	return true
}
