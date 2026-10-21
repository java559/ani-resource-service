package data_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
	controlled "github.com/zhangzhe-ctrl/ani-resource-service/tests/net05a/provider"
	"k8s.io/client-go/rest"
)

// Historical v1 rows are accepted before the simulated profile upgrade. Only
// the infrastructure boundary's method set changes; authorization, admission,
// transactions and the KC-backed worker remain production implementations.
func acceptHistoricalKCVlanFixture(t *testing.T, f *egressFixture, provider *data.KCProvider, intent biz.PlatformIntent) (biz.PlatformResource, error) {
	t.Helper()
	if intent.Kind != "create_vlan" || intent.Vlan == nil || intent.Vlan.DeviceID == "" || intent.Vlan.VirtualSwitchID != "" {
		t.Fatal("historical KC fixture requires an explicit legacy VLAN intent")
	}
	f.p.UseEgressInfrastructure(struct{ biz.EgressInfrastructure }{provider})
	defer f.p.UseEgressInfrastructure(provider)
	return f.e.CreatePlatform(f.ctx, intent)
}

func historicalKCVlanFixture(t *testing.T, f *egressFixture, provider *data.KCProvider, intent biz.PlatformIntent) biz.PlatformResource {
	t.Helper()
	vlan, err := acceptHistoricalKCVlanFixture(t, f, provider, intent)
	if err != nil {
		t.Fatal("historical KC VLAN acceptance failed", err)
	}
	f.drive(t, func() bool {
		vlan, err = f.e.GetPlatform(f.ctx, "vlan", vlan.ID)
		if err != nil {
			t.Fatal(err)
		}
		return vlan.State == biz.Available
	})
	return vlan
}

func TestEgressKCDeviceAdoptionPartialFactsAndUntaggedVlan(t *testing.T) {
	p, owner := database(t)
	api := controlled.New()
	host := httptest.NewServer(api)
	defer host.Close()
	provider, err := data.NewKCProvider(p, &rest.Config{Host: host.URL, QPS: 1000, Burst: 1000})
	if err != nil {
		t.Fatal(err)
	}
	p.UseEgressInfrastructure(provider)
	e, err := biz.NewEgress(p, provider, biz.ContextEgressAuthorization{}, []byte("0123456789abcdef0123456789abcdef"), time.Minute, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	policy := biz.DefaultWorkerPolicy()
	policy.ObserveEvery = 50 * time.Millisecond
	policy.RetryMin = time.Millisecond
	policy.RetryMax = 10 * time.Millisecond
	w, err := biz.NewWorker(p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	f := &egressFixture{p: p, owner: owner, e: e, w: w, ctx: biz.WithEgressCaller(context.Background(), biz.EgressCaller{PlatformAdministrator: true})}
	nodes := map[string]string{"node-a": uuid.NewString(), "node-b": uuid.NewString()}
	facts := func(name string, managed bool, at time.Time) {
		uid := nodes[name]
		annotation := `{}`
		if managed {
			annotation = `{"fixture0":true}`
		}
		api.Change("nodes", map[string]any{"apiVersion": "v1", "kind": "Node", "metadata": map[string]any{"name": name, "uid": uid, "annotations": map[string]any{"networking.kubercloud.com/managed_netdevs": annotation}}}, false)
		master := ""
		if managed {
			master = "ovs-system"
		}
		doc := data.NodeFactsDocument{Version: 1, NodeName: name, NodeUID: uid, CollectedAt: at, Interfaces: []biz.NodeInterface{{NodeName: name, NodeUID: uid, Name: "fixture0", Kind: "device", MAC: "02:00:00:00:00:01", Master: master, OVSManaged: managed, KCBridgeReady: managed, LinkUp: true, Carrier: true}}}
		body, _ := json.Marshal(doc)
		api.Change("configmaps", map[string]any{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "facts-" + name, "namespace": "kcn-system", "uid": "facts-uid-" + name, "labels": map[string]any{"network.ani.io/managed-by": "ani-network-node-facts", "network.ani.io/node-uid": uid}}, "data": map[string]any{"facts.json": string(body)}}, false)
	}
	facts("node-a", false, time.Now())
	facts("node-b", false, time.Now())
	api.Change("configmaps", map[string]any{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "kcn-config", "namespace": "kcn-system", "uid": "shared-config-uid", "annotations": map[string]any{"other-owner": "preserved"}}, "data": map[string]any{"managedDevices": "existing0", "other-setting": "preserved"}}, false)
	inv, err := e.ListNodeInterfaces(f.ctx, "")
	if err != nil || len(inv.Items) != 2 || !inv.Items[0].Selectable || !inv.Items[1].Selectable {
		t.Fatal("fixture device discovery", inv, err)
	}
	device, err := e.CreatePlatform(f.ctx, biz.PlatformIntent{Kind: "adopt_device", Name: "fixture-adoption", IdempotencyKey: "adopt", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = w.Step(f.ctx); err != nil {
		t.Fatal(err)
	}
	current, err := e.GetPlatform(f.ctx, "device", device.ID)
	if err != nil || current.State == biz.Available {
		t.Fatal("unapplied device claimed ready", err)
	}
	cm := api.Object("configmaps", "kcn-system", "kcn-config")
	if cm["data"].(map[string]any)["managedDevices"] != "existing0,fixture0" || cm["data"].(map[string]any)["other-setting"] != "preserved" || cm["metadata"].(map[string]any)["annotations"].(map[string]any)["other-owner"] != "preserved" {
		t.Fatal("device adoption clobbered shared config", cm)
	}
	facts("node-a", true, time.Now())
	f.drive(t, func() bool {
		v, err := e.GetPlatform(f.ctx, "device", device.ID)
		if err != nil {
			return false
		}
		n := 0
		for _, item := range v.Device.Nodes {
			if item.KCManaged {
				n++
			}
		}
		return n == 1 && v.State != biz.Available
	})
	partial := biz.PlatformIntent{Kind: "create_vlan", Name: "too-early", IdempotencyKey: "too-early", Vlan: &biz.VlanConfig{DeviceID: device.ID, VlanID: 0}}
	if _, err = e.CreatePlatform(f.ctx, partial); biz.ReasonOf(err) != biz.InvalidArgument {
		t.Fatal("current profile admitted new v1 VLAN", err)
	}
	if _, err = acceptHistoricalKCVlanFixture(t, f, provider, partial); biz.ReasonOf(err) != biz.PublicEgressNotReady {
		t.Fatal("partial adoption admitted VLAN", err)
	}
	facts("node-b", true, time.Now())
	f.drive(t, func() bool {
		v, err := e.GetPlatform(f.ctx, "device", device.ID)
		return err == nil && v.State == biz.Available
	})
	legacyIntent := biz.PlatformIntent{Kind: "create_vlan", Name: "untagged", IdempotencyKey: "vlan", Vlan: &biz.VlanConfig{DeviceID: device.ID, VlanID: 0}}
	vlan := historicalKCVlanFixture(t, f, provider, legacyIntent)
	replay, err := e.CreatePlatform(f.ctx, legacyIntent)
	if err != nil || replay.ID != vlan.ID || replay.LastOperationID != vlan.LastOperationID {
		t.Fatal("current profile did not preserve exact legacy VLAN replay", replay, err)
	}
	cr := api.Object("vlannetworks", "", "vlan-"+vlan.ID[5:])
	if fmt.Sprint(cr["spec"].(map[string]any)["vlanID"]) != "0" || cr["spec"].(map[string]any)["devName"] != "fixture0" {
		t.Fatal("VLAN zero render", cr)
	}
	facts("node-b", true, time.Now().Add(-2*time.Minute))
	f.drive(t, func() bool {
		v, err := e.GetPlatform(f.ctx, "vlan", vlan.ID)
		return err == nil && v.State == biz.Degraded
	})
}

const preManagedDevices = "existing0, fixture0"

func preManagedDeviceFixture(t *testing.T) (*egressFixture, *controlled.Server) {
	f, api, _ := preManagedDeviceFixtureWithHandler(t, nil)
	return f, api
}

func preManagedDeviceFixtureWithHandler(t *testing.T, wrap func(http.Handler) http.Handler) (*egressFixture, *controlled.Server, *data.KCProvider) {
	t.Helper()
	p, owner := database(t)
	api := controlled.New()
	var handler http.Handler = api
	if wrap != nil {
		handler = wrap(handler)
	}
	host := httptest.NewServer(handler)
	t.Cleanup(host.Close)
	provider, err := data.NewKCProvider(p, &rest.Config{Host: host.URL, QPS: 1000, Burst: 1000})
	if err != nil {
		t.Fatal(err)
	}
	p.UseEgressInfrastructure(provider)
	e, err := biz.NewEgress(p, provider, biz.ContextEgressAuthorization{}, []byte("0123456789abcdef0123456789abcdef"), time.Minute, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	policy := biz.DefaultWorkerPolicy()
	policy.ObserveEvery = 50 * time.Millisecond
	policy.RetryMin = time.Millisecond
	policy.RetryMax = 10 * time.Millisecond
	w, err := biz.NewWorker(p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	f := &egressFixture{p: p, owner: owner, e: e, w: w, ctx: biz.WithEgressCaller(context.Background(), biz.EgressCaller{PlatformAdministrator: true})}
	for i, name := range []string{"node-a", "node-b"} {
		uid := uuid.NewString()
		api.Change("nodes", map[string]any{"apiVersion": "v1", "kind": "Node", "metadata": map[string]any{"name": name, "uid": uid, "annotations": map[string]any{"networking.kubercloud.com/managed_netdevs": `{"fixture0":true}`}}}, false)
		doc := data.NodeFactsDocument{Version: 1, NodeName: name, NodeUID: uid, CollectedAt: time.Now(), Interfaces: []biz.NodeInterface{{NodeName: name, NodeUID: uid, Name: "fixture0", Kind: "device", MAC: fmt.Sprintf("02:00:00:00:00:%02x", i+1), Master: "ovs-system", OVSManaged: true, KCBridgeReady: true, LinkUp: true, Carrier: true}}}
		body, _ := json.Marshal(doc)
		api.Change("configmaps", map[string]any{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "facts-" + name, "namespace": "kcn-system", "uid": "facts-uid-" + name, "labels": map[string]any{"network.ani.io/managed-by": "ani-network-node-facts", "network.ani.io/node-uid": uid}}, "data": map[string]any{"facts.json": string(body)}}, false)
	}
	api.Change("configmaps", map[string]any{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "kcn-config", "namespace": "kcn-system", "uid": "installer-config-uid", "annotations": map[string]any{"installer": "preserved"}}, "data": map[string]any{"managedDevices": preManagedDevices, "other-setting": "preserved"}}, false)
	return f, api, provider
}

func TestEgressKCPreManagedDeviceRegistrationAndUntaggedVlan(t *testing.T) {
	f, api, provider := preManagedDeviceFixtureWithHandler(t, nil)
	e := f.e
	inv, err := e.ListNodeInterfaces(f.ctx, "")
	if err != nil || len(inv.Items) != 2 {
		t.Fatal("pre-managed discovery failed", inv, err)
	}
	intent := biz.PlatformIntent{Kind: "adopt_device", Name: "existing-kc-device", IdempotencyKey: "register-existing", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}}
	device, err := e.CreatePlatform(f.ctx, intent)
	if err != nil {
		t.Fatal("installer-prepared kc device must be reusable through the platform API", err)
	}
	f.drive(t, func() bool {
		v, err := e.GetPlatform(f.ctx, "device", device.ID)
		return err == nil && v.State == biz.Available
	})
	vlan := historicalKCVlanFixture(t, f, provider, biz.PlatformIntent{Kind: "create_vlan", Name: "existing-device-untagged", IdempotencyKey: "vlan-zero", Vlan: &biz.VlanConfig{DeviceID: device.ID, VlanID: 0}})
	cr := api.Object("vlannetworks", "", "vlan-"+vlan.ID[5:])
	if fmt.Sprint(cr["spec"].(map[string]any)["vlanID"]) != "0" || cr["spec"].(map[string]any)["devName"] != "fixture0" {
		t.Fatal("existing device did not create untagged VLAN", cr)
	}
	cm := api.Object("configmaps", "kcn-system", "kcn-config")
	if cm["data"].(map[string]any)["managedDevices"] != preManagedDevices || cm["data"].(map[string]any)["other-setting"] != "preserved" || cm["metadata"].(map[string]any)["annotations"].(map[string]any)["installer"] != "preserved" {
		t.Fatal("registration rewrote installer-managed configuration", cm)
	}
	// A fresh API request must not create a second registration for the same
	// physical device, while replay still returns the original acceptance.
	inv, err = e.ListNodeInterfaces(f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = e.CreatePlatform(f.ctx, biz.PlatformIntent{Kind: "adopt_device", Name: "duplicate", IdempotencyKey: "duplicate", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}}); biz.ReasonOf(err) != biz.ResourceInUse {
		t.Fatal("already registered device was not protected", err)
	}
	replay, err := e.CreatePlatform(f.ctx, intent)
	if err != nil || replay.ID != device.ID || replay.LastOperationID != device.LastOperationID {
		t.Fatal("registration replay changed accepted identity", replay, err)
	}
	// The node name and interface name alone cannot authorize a new NIC.
	facts := api.Object("configmaps", "kcn-system", "facts-node-a")
	var changed data.NodeFactsDocument
	if json.Unmarshal([]byte(facts["data"].(map[string]any)["facts.json"].(string)), &changed) != nil {
		t.Fatal("invalid fixture facts")
	}
	changed.Interfaces[0].MAC = "02:ff:ff:ff:ff:ff"
	body, _ := json.Marshal(changed)
	facts["data"].(map[string]any)["facts.json"] = string(body)
	api.Change("configmaps", facts, false)
	f.drive(t, func() bool {
		v, err := e.GetPlatform(f.ctx, "device", device.ID)
		return err == nil && v.State == biz.Degraded
	})
}

func TestEgressKCPreManagedRegistrationFencesConfigurationChanges(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(map[string]any)
		ready  bool
	}{
		{"config_replaced", func(cm map[string]any) { cm["metadata"].(map[string]any)["uid"] = "replacement" }, false},
		{"device_removed", func(cm map[string]any) { cm["data"].(map[string]any)["managedDevices"] = "existing0" }, false},
		{"unrelated_annotation", func(cm map[string]any) {
			cm["metadata"].(map[string]any)["annotations"].(map[string]any)["other-administrator"] = "preserved"
		}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f, api := preManagedDeviceFixture(t)
			inv, err := f.e.ListNodeInterfaces(f.ctx, "")
			if err != nil {
				t.Fatal(err)
			}
			device, err := f.e.CreatePlatform(f.ctx, biz.PlatformIntent{Kind: "adopt_device", Name: "prepared-device", IdempotencyKey: "register", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}})
			if err != nil {
				t.Fatal(err)
			}
			cm := api.Object("configmaps", "kcn-system", "kcn-config")
			tc.mutate(cm)
			api.Change("configmaps", cm, false)
			f.drive(t, func() bool {
				v, err := f.e.GetPlatform(f.ctx, "device", device.ID)
				if err != nil {
					return false
				}
				if tc.ready {
					return v.State == biz.Available
				}
				return v.Reason == biz.ProviderOwnership
			})
			actual := api.Object("configmaps", "kcn-system", "kcn-config")
			if actual["data"].(map[string]any)["managedDevices"] != cm["data"].(map[string]any)["managedDevices"] {
				t.Fatal("registration rewrote current managedDevices", actual)
			}
			annotations := actual["metadata"].(map[string]any)["annotations"].(map[string]any)
			if !tc.ready && len(annotations) != 1 {
				t.Fatal("changed identity was claimed", annotations)
			}
			if tc.ready && annotations["other-administrator"] != "preserved" {
				t.Fatal("concurrent unrelated configuration was lost", annotations)
			}
		})
	}
}

// A complete cluster audit can take longer than its refresh interval over a
// remote API connection. Dependent device checks must share that same view;
// collecting another full view inside the first read can starve every attempt.
func TestEgressKCManagedVlanWithAuditLongerThanRefreshInterval(t *testing.T) {
	f, api, provider := preManagedDeviceFixtureWithHandler(t, func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodGet && r.URL.Path == "/api/v1/configmaps" && r.URL.Query().Get("watch") != "true" {
				select {
				case <-time.After(300 * time.Millisecond):
				case <-r.Context().Done():
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	})
	inv, err := f.e.ListNodeInterfaces(f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	device := f.platform(t, biz.PlatformIntent{Kind: "adopt_device", Name: "slow-audit-device", IdempotencyKey: "slow-audit-device", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}})
	options := data.DefaultObservationOptions()
	options.AuditInterval = 20 * time.Millisecond
	options.AuditJitter = time.Millisecond
	options.AuditTimeout = 2 * time.Second
	// Exercise actual complete shared audits without a periodic background
	// collector, so each lifecycle request owns a reproducible latency boundary.
	if _, err = provider.EnableObservation(options); err != nil {
		t.Fatal(err)
	}
	policy := biz.DefaultWorkerPolicy()
	policy.RequestTimeout = 500 * time.Millisecond
	policy.Lease = 2 * time.Second
	policy.ObserveEvery = 50 * time.Millisecond
	policy.RetryMin = time.Millisecond
	policy.RetryMax = 10 * time.Millisecond
	f.w, err = biz.NewWorker(f.p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	vlan, err := acceptHistoricalKCVlanFixture(t, f, provider, biz.PlatformIntent{Kind: "create_vlan", Name: "slow-audit-vlan", IdempotencyKey: "slow-audit-vlan", Vlan: &biz.VlanConfig{DeviceID: device.ID, VlanID: 0}})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		if _, err = f.w.Step(f.ctx); err != nil {
			t.Fatal(err)
		}
		vlan, err = f.e.GetPlatform(f.ctx, "vlan", vlan.ID)
		if err != nil {
			t.Fatal(err)
		}
		if vlan.State == biz.Available {
			break
		}
	}
	if vlan.State != biz.Available {
		t.Fatalf("bounded audit stalled VLAN before source freshness expires: state=%s reason=%s", vlan.State, vlan.Reason)
	}
	if api.Object("vlannetworks", "", "vlan-"+vlan.ID[5:]) == nil {
		t.Fatal("VLAN never reached the provider")
	}
	facts := api.Object("configmaps", "kcn-system", "facts-node-a")
	var doc data.NodeFactsDocument
	if err = json.Unmarshal([]byte(facts["data"].(map[string]any)["facts.json"].(string)), &doc); err != nil {
		t.Fatal(err)
	}
	doc.Interfaces[0].MAC = "02:ff:ff:ff:ff:ff"
	body, _ := json.Marshal(doc)
	facts["data"].(map[string]any)["facts.json"] = string(body)
	api.Change("configmaps", facts, false)
	f.drive(t, func() bool {
		v, err := f.e.GetPlatform(f.ctx, "vlan", vlan.ID)
		return err == nil && v.State == biz.Degraded
	})
}

func TestEgressKCManagedVlanProgressesWhileFactsAreRenewed(t *testing.T) {
	f, api, provider := preManagedDeviceFixtureWithHandler(t, func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodGet && r.URL.Path == "/api/v1/configmaps" && r.URL.Query().Get("watch") != "true" {
				select {
				case <-time.After(200 * time.Millisecond):
				case <-r.Context().Done():
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	})
	renew := func() {
		for _, name := range []string{"node-a", "node-b"} {
			facts := api.Object("configmaps", "kcn-system", "facts-"+name)
			var doc data.NodeFactsDocument
			if err := json.Unmarshal([]byte(facts["data"].(map[string]any)["facts.json"].(string)), &doc); err != nil {
				panic(err)
			}
			doc.CollectedAt = time.Now().UTC()
			for i := range doc.Interfaces {
				doc.Interfaces[i].ObservedAt = doc.CollectedAt
			}
			body, _ := json.Marshal(doc)
			facts["data"].(map[string]any)["facts.json"] = string(body)
			api.Change("configmaps", facts, false)
		}
	}
	renew()
	inv, err := f.e.ListNodeInterfaces(f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	device := f.platform(t, biz.PlatformIntent{Kind: "adopt_device", Name: "renewed-device", IdempotencyKey: "renewed-device", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}})
	options := data.DefaultObservationOptions()
	options.AuditInterval, options.AuditJitter = 50*time.Millisecond, time.Millisecond
	options.AuditTimeout, options.FlushInterval = 2*time.Second, 10*time.Millisecond
	observer, err := provider.EnableObservation(options)
	if err != nil {
		t.Fatal(err)
	}
	live, cancel := context.WithCancel(f.ctx)
	done := make(chan error, 1)
	go func() { done <- observer.Start(live) }()
	defer func() {
		cancel()
		if err := <-done; err != nil {
			t.Error(err)
		}
	}()
	awaitNET05A(t, 3*time.Second, func() bool { return observer.Snapshot()["source_synced"] == 1 })
	renewCtx, stopRenewing := context.WithCancel(live)
	renewDone := make(chan struct{})
	go func() {
		defer close(renewDone)
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-renewCtx.Done():
				return
			case <-ticker.C:
				renew()
			}
		}
	}()
	defer func() { stopRenewing(); <-renewDone }()
	policy := biz.DefaultWorkerPolicy()
	policy.RequestTimeout, policy.Lease = 3*time.Second, 10*time.Second
	policy.ObserveEvery, policy.RetryMin, policy.RetryMax = 50*time.Millisecond, time.Millisecond, 10*time.Millisecond
	f.w, err = biz.NewWorker(f.p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	vlan, err := acceptHistoricalKCVlanFixture(t, f, provider, biz.PlatformIntent{Kind: "create_vlan", Name: "renewed-vlan", IdempotencyKey: "renewed-vlan", Vlan: &biz.VlanConfig{DeviceID: device.ID, VlanID: 0}})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		if _, err = f.w.Step(f.ctx); err != nil {
			t.Fatal(err)
		}
		vlan, err = f.e.GetPlatform(f.ctx, "vlan", vlan.ID)
		if err != nil {
			t.Fatal(err)
		}
		if vlan.State == biz.Available {
			break
		}
	}
	if vlan.State != biz.Available {
		t.Fatalf("stable facts renewed during audit starved VLAN: state=%s reason=%s", vlan.State, vlan.Reason)
	}
	if api.Object("vlannetworks", "", "vlan-"+vlan.ID[5:]) == nil {
		t.Fatal("VLAN never reached provider")
	}
}

func TestEgressKCDeviceFactsCompletedDuringReadAreFresh(t *testing.T) {
	var updating, future atomic.Bool
	f, _, _ := preManagedDeviceFixtureWithHandler(t, func(next http.Handler) http.Handler {
		api := next.(*controlled.Server)
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if updating.Load() && r.Method == http.MethodGet && r.URL.Path == "/api/v1/namespaces/kcn-system/configmaps/kcn-config" {
				time.Sleep(20 * time.Millisecond)
				for _, name := range []string{"node-a", "node-b"} {
					facts := api.Object("configmaps", "kcn-system", "facts-"+name)
					var doc data.NodeFactsDocument
					if err := json.Unmarshal([]byte(facts["data"].(map[string]any)["facts.json"].(string)), &doc); err != nil {
						panic(err)
					}
					doc.CollectedAt = time.Now()
					if future.Load() {
						doc.CollectedAt = doc.CollectedAt.Add(time.Minute)
					}
					body, _ := json.Marshal(doc)
					facts["data"].(map[string]any)["facts.json"] = string(body)
					api.Change("configmaps", facts, false)
				}
			}
			next.ServeHTTP(w, r)
		})
	})
	inv, err := f.e.ListNodeInterfaces(f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	device := f.platform(t, biz.PlatformIntent{Kind: "adopt_device", Name: "during-read", IdempotencyKey: "during-read", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}})
	updating.Store(true)
	observe := func(want biz.ResourceState) {
		t.Helper()
		time.Sleep(60 * time.Millisecond)
		if ran, err := f.w.Step(f.ctx); err != nil || !ran {
			t.Fatal("device observation was not run", ran, err)
		}
		v, err := f.e.GetPlatform(f.ctx, "device", device.ID)
		if err != nil || v.State != want {
			t.Fatalf("device observation state=%s want=%s err=%v", v.State, want, err)
		}
	}
	observe(biz.Available)
	future.Store(true)
	observe(biz.Degraded)
}
