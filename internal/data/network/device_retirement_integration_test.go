package data_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
	controlled "github.com/zhangzhe-ctrl/ani-resource-service/tests/net05a/provider"
	jsonpatch "gopkg.in/evanphx/json-patch.v4"
)

func TestLegacyDeviceRetirementFencesUnknownAdoptionAndReplays(t *testing.T) {
	var retirementPatches atomic.Int64
	var loseResponse atomic.Bool
	f, api, provider := preManagedDeviceFixtureWithHandler(t, func(next http.Handler) http.Handler {
		boundary := next.(*controlled.Server)
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != "PATCH" || r.URL.Query().Get("fieldManager") != "ani-resource-device-retirement" {
				next.ServeHTTP(w, r)
				return
			}
			// This is the external Kubernetes boundary only. The production
			// repository, authorization, leases, pending and stepper all run below.
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Error(err)
				http.Error(w, "read", 400)
				return
			}
			patch, err := jsonpatch.DecodePatch(body)
			if err != nil {
				t.Error(err)
				http.Error(w, "patch", 400)
				return
			}
			current := boundary.Object("configmaps", "kcn-system", "kcn-config")
			encoded, _ := json.Marshal(current)
			updated, err := patch.Apply(encoded)
			if err != nil {
				http.Error(w, `{"apiVersion":"v1","kind":"Status","status":"Failure","reason":"Conflict","code":409}`, 409)
				return
			}
			var object map[string]any
			_ = json.Unmarshal(updated, &object)
			boundary.Change("configmaps", object, false)
			retirementPatches.Add(1)
			if loseResponse.CompareAndSwap(true, false) {
				http.Error(w, `{"apiVersion":"v1","kind":"Status","status":"Failure","reason":"Timeout","code":504}`, 504)
				return
			}
			_ = json.NewEncoder(w).Encode(boundary.Object("configmaps", "kcn-system", "kcn-config"))
		})
	})
	for _, name := range []string{"node-a", "node-b"} {
		n := api.Object("nodes", "", name)
		n["metadata"].(map[string]any)["labels"] = map[string]any{"networking.kubercloud.com/allocated": "true"}
		api.Change("nodes", n, false)
	}
	inv, err := f.e.ListNodeInterfaces(f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	intent := biz.PlatformIntent{Kind: "adopt_device", Name: "legacy-owned-device", IdempotencyKey: "legacy-adopt", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}}
	device, err := f.e.CreatePlatform(f.ctx, intent)
	if err != nil {
		t.Fatal(err)
	}
	old, found, err := f.p.Claim(f.ctx, uuid.NewString(), 20*time.Second)
	if err != nil || !found {
		t.Fatal(found, err)
	}
	if err = f.p.BeginMutation(f.ctx, old, "create", ""); err != nil {
		t.Fatal(err)
	}
	target := biz.ProviderTarget{Kind: "device", ResourceID: device.ID, BindingID: old.BindingID, Requirement: old.Requirement}
	if _, err = provider.EnsureEgress(f.ctx, target); err != nil {
		t.Fatal("actual legacy adoption boundary failed", err)
	}
	// Drop the worker acknowledgement: pending=create and the old lease remain.
	retire := biz.DeviceRetirementIntent{DeviceID: device.ID, ExpectedVersion: device.Version, IdempotencyKey: "retire-original"}
	if _, err = f.e.RetireNetworkDevice(context.Background(), retire); biz.ReasonOf(err) != biz.PermissionDenied {
		t.Fatal("missing platform authorization admitted", err)
	}
	accepted, err := f.e.RetireNetworkDevice(f.ctx, retire)
	if err != nil {
		t.Fatal(err)
	}
	if accepted.State != "closing" || !accepted.PendingUnknown || accepted.FenceVersion <= device.Version || accepted.ConfigUID != "installer-config-uid" {
		t.Fatal(accepted)
	}
	// Run the real informer -> NotifyPlatform -> PostgreSQL path. A scheduling
	// hint may change next_run_at, but cannot reopen a closed registration.
	options := data.DefaultObservationOptions()
	options.AuditInterval, options.AuditJitter = 50*time.Millisecond, time.Millisecond
	options.AuditTimeout, options.FlushInterval = 2*time.Second, 10*time.Millisecond
	observer, err := provider.EnableObservation(options)
	if err != nil {
		t.Fatal(err)
	}
	live, stopObservation := context.WithCancel(f.ctx)
	observationDone := make(chan error, 1)
	go func() { observationDone <- observer.Start(live) }()
	defer func() {
		stopObservation()
		if err := <-observationDone; err != nil {
			t.Error(err)
		}
	}()
	var executorEpoch int64
	if err = f.owner.QueryRow(f.ctx, "SELECT executor_epoch FROM network_legacy_device_retirements WHERE resource_id=$1", device.ID).Scan(&executorEpoch); err != nil {
		t.Fatal(err)
	}
	assertExecutorFenced := func(wantState string, wantRetired bool, notificationsBefore float64) {
		t.Helper()
		awaitNET05A(t, 3*time.Second, func() bool {
			return observer.Snapshot()["source_synced"] == 1 && observer.Snapshot()["notifications_total"] > notificationsBefore
		})
		var owner *string
		var leaseUntil *time.Time
		var epoch, generation int64
		var closed, retired, due bool
		var state string
		if err := f.owner.QueryRow(f.ctx, `SELECT x.lease_owner,x.lease_until,x.lease_epoch,x.requested_generation,
 x.next_run_at<=clock_timestamp(),d.registration_closed,d.retired,r.state
 FROM network_platform_reconciliations x JOIN network_device_adoptions d USING(resource_id)
 JOIN network_platform_resources r USING(resource_id) WHERE x.resource_id=$1`, device.ID).Scan(&owner, &leaseUntil, &epoch, &generation, &due, &closed, &retired, &state); err != nil {
			t.Fatal(err)
		}
		t.Logf("watch reached closed executor: state=%s generation=%d scheduling_hint_due=%v epoch=%d", state, generation, due, epoch)
		if owner != nil || leaseUntil != nil || epoch != executorEpoch || !closed || retired != wantRetired || state != wantState {
			t.Fatal("watch reopened the closed executor", owner, leaseUntil, epoch, executorEpoch, closed, retired, state)
		}
		if work, found, err := f.p.Claim(f.ctx, uuid.NewString(), time.Second); err != nil || found {
			t.Fatal("closed registration was claimable after watch", found, work, err)
		}
		if err := f.p.Finish(f.ctx, old, biz.Progress{State: biz.Available, Observed: true, ClearPending: true}); !errors.Is(err, biz.ErrLeaseLost) {
			t.Fatal("late legacy worker was not fenced after watch", err)
		}
		if err := provider.EnsureLegacyDeviceMutationAllowed(f.ctx, target); err == nil {
			t.Fatal("closed registration allowed a late dispatch after watch")
		}
	}
	assertExecutorFenced("deleting", false, 0)
	policy := biz.DefaultWorkerPolicy()
	policy.ObserveEvery = 10 * time.Millisecond
	stepper, err := biz.NewLegacyDeviceRetirementStepper(f.p, provider, uuid.NewString(), policy)
	if err != nil {
		t.Fatal(err)
	}
	if worked, err := stepper.Step(f.ctx); err != nil || worked {
		t.Fatal("cleanup ran before persisted drain bound", worked, err)
	}
	native := map[string]any{"apiVersion": "networking.kubercloud.com/v1", "kind": "VlanNetwork", "metadata": map[string]any{"name": "native-legacy", "uid": "native-uid"}, "spec": map[string]any{"devName": "fixture0", "vClusterName": "native-group", "vlanID": 0}}
	api.Change("vlannetworks", native, false)
	var drain time.Time
	if err = f.owner.QueryRow(f.ctx, "SELECT drain_after FROM network_legacy_device_retirements WHERE resource_id=$1", device.ID).Scan(&drain); err != nil {
		t.Fatal(err)
	}
	if wait := time.Until(drain); wait > 0 {
		time.Sleep(wait + time.Millisecond)
	}
	if _, err = stepper.Step(f.ctx); err != nil {
		t.Fatal(err)
	}
	blocked, err := f.e.GetNetworkDeviceRetirement(f.ctx, device.ID)
	if err != nil || blocked.State != "blocked" || retirementPatches.Load() != 0 {
		t.Fatal(blocked, retirementPatches.Load(), err)
	}
	// External KCN deletion convergence is supplied at the Kubernetes boundary;
	// no business row, authorization decision or pending state is bypassed.
	api.Change("vlannetworks", native, true)
	for _, name := range []string{"node-a", "node-b"} {
		cm := api.Object("configmaps", "kcn-system", "facts-"+name)
		var doc data.NodeFactsDocument
		_ = json.Unmarshal([]byte(cm["data"].(map[string]any)["facts.json"].(string)), &doc)
		doc.CollectedAt = time.Now()
		doc.OVSBridgeMappingsObserved = true
		doc.OVSBridgeMappings = map[string]string{}
		doc.Interfaces[0].Master = ""
		doc.Interfaces[0].OVSManaged = false
		doc.Interfaces[0].KCBridgeReady = false
		doc.Interfaces[0].KCBridgeNetworks = nil
		body, _ := json.Marshal(doc)
		cm["data"].(map[string]any)["facts.json"] = string(body)
		api.Change("configmaps", cm, false)
	}
	loseResponse.Store(true)
	deadline := time.Now().Add(4 * time.Second)
	var final biz.DeviceRetirement
	for time.Now().Before(deadline) {
		if _, err = stepper.Step(f.ctx); err != nil {
			t.Fatal(err)
		}
		final, err = f.e.GetNetworkDeviceRetirement(f.ctx, device.ID)
		if err != nil {
			t.Fatal(err)
		}
		if final.State == "retired" {
			break
		}
		time.Sleep(12 * time.Millisecond)
	}
	if final.State != "retired" || final.PendingUnknown || final.CompletedAt == nil || retirementPatches.Load() != 1 {
		t.Fatal("unknown CAS did not converge exactly once", final, retirementPatches.Load())
	}
	cm := api.Object("configmaps", "kcn-system", "kcn-config")
	if cm["data"].(map[string]any)["managedDevices"] != preManagedDevices || cm["metadata"].(map[string]any)["annotations"].(map[string]any)["installer"] != "preserved" {
		t.Fatal("external original configuration was changed", cm)
	}
	var retired, closed bool
	var pending, state string
	if err = f.owner.QueryRow(f.ctx, "SELECT d.retired,d.registration_closed,p.pending_action,p.state FROM network_device_adoptions d JOIN network_platform_resources p USING(resource_id) WHERE d.resource_id=$1", device.ID).Scan(&retired, &closed, &pending, &state); err != nil || !retired || !closed || pending != "" || state != "deleted" {
		t.Fatal(retired, closed, pending, state, err)
	}
	replay, err := f.e.RetireNetworkDevice(f.ctx, retire)
	if err != nil || replay.OperationID != accepted.OperationID || replay.DeviceID != accepted.DeviceID || replay.State != accepted.State || replay.Version != accepted.Version || replay.PendingUnknown != accepted.PendingUnknown || !replay.CreatedAt.Equal(accepted.CreatedAt) {
		t.Fatal("exact retirement replay changed", replay, accepted, err)
	}
	changed := retire
	changed.ExpectedVersion++
	if _, err = f.e.RetireNetworkDevice(f.ctx, changed); biz.ReasonOf(err) != biz.IdempotencyConflict {
		t.Fatal("same key accepted different version", err)
	}
	oldReplay, err := f.e.CreatePlatform(f.ctx, intent)
	if err != nil || oldReplay.ID != device.ID || oldReplay.LastOperationID != device.LastOperationID {
		t.Fatal("old adoption replay changed", oldReplay, err)
	}
	notificationsBefore := observer.Snapshot()["notifications_total"]
	node := api.Object("nodes", "", "node-a")
	node["metadata"].(map[string]any)["annotations"].(map[string]any)["network.ani.io/retirement-watch-probe"] = "retired"
	api.Change("nodes", node, false)
	assertExecutorFenced("deleted", true, notificationsBefore)
	current, err := f.e.GetPlatform(f.ctx, "device", device.ID)
	if err != nil || current.State != biz.Deleted || retirementPatches.Load() != 1 {
		t.Fatal("old replay or watch resurrected retired registration", current, retirementPatches.Load(), err)
	}
}

func TestLegacyDeviceRetirementRejectsActiveProductVlan(t *testing.T) {
	f, _, provider := preManagedDeviceFixtureWithHandler(t, nil)
	inv, err := f.e.ListNodeInterfaces(f.ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	device := f.platform(t, biz.PlatformIntent{Kind: "adopt_device", Name: "legacy-with-vlan", IdempotencyKey: "adopt", Device: &biz.DeviceConfig{DeviceName: "fixture0", InventoryFingerprint: inv.Fingerprint}})
	// Construct an accepted pre-upgrade v1 reference through the real use case
	// and PostgreSQL transaction. This boundary exposes only the old profile's
	// interface, while retaining the same KC facts implementation underneath.
	f.p.UseEgressInfrastructure(struct{ biz.EgressInfrastructure }{provider})
	vlan, err := f.e.CreatePlatform(f.ctx, biz.PlatformIntent{Kind: "create_vlan", Name: "retained-vlan", IdempotencyKey: "vlan", Vlan: &biz.VlanConfig{DeviceID: device.ID, VlanID: 0}})
	f.p.UseEgressInfrastructure(provider)
	if err != nil {
		t.Fatal("historical v1 VLAN acceptance failed", err)
	}
	vlan, err = f.e.GetPlatform(f.ctx, "vlan", vlan.ID)
	if err != nil || vlan.State != biz.Provisioning || vlan.Vlan == nil || vlan.Vlan.DeviceID != device.ID {
		t.Fatal("historical active VLAN reference was not persisted", vlan, err)
	}
	if _, err = f.e.RetireNetworkDevice(f.ctx, biz.DeviceRetirementIntent{DeviceID: device.ID, ExpectedVersion: device.Version, IdempotencyKey: "retire"}); biz.ReasonOf(err) != biz.ResourceInUse {
		t.Fatal("active VLAN was ignored", err)
	}
	var closed bool
	if err = f.owner.QueryRow(f.ctx, "SELECT registration_closed FROM network_device_adoptions WHERE resource_id=$1", device.ID).Scan(&closed); err != nil || closed {
		t.Fatal("rejected retirement closed active device", closed, err)
	}
}
