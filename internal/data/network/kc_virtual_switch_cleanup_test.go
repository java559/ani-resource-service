package data

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network/sqlcgen"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func TestSwitchDeletionWaitsForExplicitPhysicalCleanup(t *testing.T) {
	for _, tc := range []struct {
		name  string
		clean bool
	}{
		{"clean", true}, {"unobserved_global_mapping", false}, {"residual_mapping_without_ovs_port", false}, {"daemon_feedback", false}, {"provider_vlan", false}, {"shared_other_group", true}, {"stale_facts", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sw, nodes, maps, _, _, now := switchFixture(t)
			member := biz.VirtualSwitchNode{NodeName: "n1", NodeUID: "uid1", Priority: 100, Allocated: true}
			var doc NodeFactsDocument
			_ = json.Unmarshal([]byte(crString(&maps[0], "data", "facts.json")), &doc)
			accepted, _ := json.Marshal(doc.Interfaces)
			devices := []sqlcgen.NetworkDeviceAdoption{{DeviceName: "ens35", NodeInventory: accepted}}
			doc.OVSBridgeMappingsObserved = true
			doc.OVSBridgeMappings = map[string]string{}
			var vlans []unstructured.Unstructured
			switch tc.name {
			case "unobserved_global_mapping":
				doc.OVSBridgeMappingsObserved = false
			case "residual_mapping_without_ovs_port":
				doc.OVSBridgeMappings[sw.GroupName+".ens35"] = "br-ens35"
			case "daemon_feedback":
				a := nodes[0].GetAnnotations()
				a[virtualSwitchGroupPrefix+sw.GroupName] = "ens35"
				nodes[0].SetAnnotations(a)
			case "provider_vlan":
				vlans = []unstructured.Unstructured{{Object: map[string]any{"spec": map[string]any{"vClusterName": sw.GroupName, "devName": "ens35"}}}}
			case "shared_other_group":
				doc.OVSBridgeMappings["protected-other.ens35"] = "br-ens35"
				doc.Interfaces[0].OVSManaged = true
				doc.Interfaces[0].Master = "ovs-system"
				doc.Interfaces[0].KCBridgeReady = true
				doc.Interfaces[0].KCBridgeNetworks = []string{"protected-other.ens35"}
			case "stale_facts":
				doc.CollectedAt = now.Add(-2 * time.Minute)
			}
			body, _ := json.Marshal(doc)
			_ = unstructured.SetNestedField(maps[0].Object, string(body), "data", "facts.json")
			clean, err := switchNodeCleanupConfirmed(sw, member, &nodes[0], maps, vlans, devices, now)
			if clean != tc.clean || (tc.clean && err != nil) {
				t.Fatal(clean, err)
			}
		})
	}
}

func TestSwitchInventoryPreservesMissingAndUnavailableMembers(t *testing.T) {
	sw, nodes, maps, daemons, config, now := switchFixture(t)
	extra := nodes[0].DeepCopy()
	extra.SetName("n2")
	extra.SetUID("uid2")
	nodes = append(nodes, *extra)
	var doc NodeFactsDocument
	_ = json.Unmarshal([]byte(crString(&maps[0], "data", "facts.json")), &doc)
	other := doc.Interfaces[0]
	other.Name = "ens36"
	other.MAC = "00:11:22:33:44:66"
	other.Management = true
	doc.Interfaces = append(doc.Interfaces, other)
	body, _ := json.Marshal(doc)
	_ = unstructured.SetNestedField(maps[0].Object, string(body), "data", "facts.json")
	inv := virtualSwitchInterfaceInventory("cluster", sw, nodes, maps, nil, daemons, config, now, now)
	if len(inv.Items) != 3 || len(inv.Fingerprint) != 64 {
		t.Fatal(inv)
	}
	seen := map[string]bool{}
	for _, v := range inv.Items {
		seen[v.NodeUID+"/"+v.Name] = true
		if (v.Name == "ens36" || v.NodeUID == "uid2") && (v.Selectable || len(v.UnavailableReasons) == 0) {
			t.Fatal("unavailable item hidden or selectable", v)
		}
	}
	if !seen["uid1/ens35"] || !seen["uid1/ens36"] || !seen["uid2/"] {
		t.Fatal(seen)
	}
	sw.Nodes = []biz.VirtualSwitchNode{{NodeName: "gone", NodeUID: "gone-uid", Priority: 100, Allocated: true}}
	inv = virtualSwitchInterfaceInventory("cluster", sw, nodes, maps, nil, daemons, config, now, now)
	found := false
	for _, v := range inv.Items {
		if v.NodeUID == "gone-uid" {
			found = true
			if v.Selectable || len(v.UnavailableReasons) == 0 {
				t.Fatal(v)
			}
		}
	}
	if !found {
		t.Fatal("missing frozen member disappeared")
	}
}

func TestGlobalOVSMappingsProveAnEmptyCollection(t *testing.T) {
	for _, tc := range []struct {
		raw   string
		valid bool
	}{
		{"", true}, {"other.ens36:br-ens36", true}, {"group.ens35:br-ens35,group.ens35:br-other", false}, {"missing-target", false},
	} {
		t.Run(tc.raw, func(t *testing.T) {
			c := NodeFactsCollector{Run: func(context.Context, string, ...string) ([]byte, error) {
				return []byte(fmt.Sprintf(`{"headings":["external_ids"],"data":[[["map",[["ovn-bridge-mappings",%q]]]]]}`, tc.raw)), nil
			}}
			got, err := c.collectOVSBridgeMappings(context.Background())
			if (err == nil) != tc.valid {
				t.Fatal(got, err)
			}
			if tc.valid && tc.raw == "" && (got == nil || len(got) != 0) {
				t.Fatal("empty collection lacks explicit evidence", got)
			}
		})
	}
}
