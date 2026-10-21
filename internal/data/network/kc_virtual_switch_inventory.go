package data

import (
	"context"
	"encoding/json"
	"net"
	"net/netip"
	"slices"
	"strings"
	"time"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

// Discovery always returns the entire allocated membership and every collected
// interface. The device-specific admission method separately proves one device
// on every frozen node; a diagnostic subset cannot supply that proof.
func (p *KCProvider) ListVirtualSwitchInterfaces(ctx context.Context, cluster string, sw biz.VirtualSwitch) (biz.InterfaceInventory, error) {
	if cluster == "" || cluster != p.repository.placement.ClusterID || (sw.ClusterID != "" && sw.ClusterID != cluster) {
		return biz.InterfaceInventory{}, switchConflict()
	}
	nodes, maps, vlans, collected, err := p.nodeFactsObjects(ctx)
	if err != nil {
		return biz.InterfaceInventory{}, err
	}
	daemons, err := p.listAll(ctx, pods)
	if err != nil {
		return biz.InterfaceInventory{}, err
	}
	config, err := p.client.Resource(kcConfigMaps).Namespace(kcSystemNamespace).Get(ctx, "kcn-config", metav1.GetOptions{})
	if err != nil {
		return biz.InterfaceInventory{}, readFailure(err)
	}
	now, err := p.repository.queries.DatabaseTime(ctx)
	if err != nil {
		return biz.InterfaceInventory{}, readFailure(err)
	}
	return virtualSwitchInterfaceInventory(cluster, sw, nodes, maps, vlans, daemons, config, now, collected), nil
}

func virtualSwitchInterfaceInventory(cluster string, sw biz.VirtualSwitch, nodes, maps, vlans, daemons []unstructured.Unstructured, config *unstructured.Unstructured, now, collected time.Time) biz.InterfaceInventory {
	result := biz.InterfaceInventory{CollectedAt: collected, Items: []biz.NodeInterface{}}
	membershipErr := validateSwitchMembers(sw, nodes)
	frozen := map[string]biz.VirtualSwitchNode{}
	seen := map[string]bool{}
	for _, n := range sw.Nodes {
		frozen[n.NodeUID] = n
	}
	for _, node := range nodes {
		_, bound := frozen[string(node.GetUID())]
		if node.GetLabels()[allocatedNodeLabel] != "true" && !bound {
			continue
		}
		uid := string(node.GetUID())
		sources := 0
		var doc NodeFactsDocument
		seen[uid] = true
		valid := true
		for _, cm := range maps {
			if cm.GetNamespace() != kcSystemNamespace || cm.GetLabels()[ownerLabel] != factsOwner || cm.GetLabels()[factsNodeLabel] != uid {
				continue
			}
			sources++
			if cm.GetUID() == "" || cm.GetResourceVersion() == "" || cm.GetDeletionTimestamp() != nil || json.Unmarshal([]byte(crString(&cm, "data", "facts.json")), &doc) != nil || doc.Version != 1 || doc.NodeName != node.GetName() || doc.NodeUID != uid || len(doc.Interfaces) == 0 {
				valid = false
			}
		}
		if sources != 1 || !valid {
			result.Items = append(result.Items, biz.NodeInterface{NodeName: node.GetName(), NodeUID: uid, UnavailableReasons: []string{"node_facts_missing_or_invalid"}})
			continue
		}
		if doc.CollectedAt.Before(result.CollectedAt) {
			result.CollectedAt = doc.CollectedAt
		}
		owned := sw.BindingID != "" && node.GetAnnotations()[VirtualSwitchOwnerKey(sw.GroupName)] == sw.BindingID && node.GetLabels()[virtualSwitchGroupPrefix+sw.GroupName] == "100"
		names := map[string]int{}
		for _, v := range doc.Interfaces {
			names[v.Name]++
		}
		for _, v := range doc.Interfaces {
			v.ObservedAt = doc.CollectedAt
			v.Selectable = false
			v.UnavailableReasons = nil
			v.VlanNetworkIDs = nil
			reject := func(reason string) { v.UnavailableReasons = append(v.UnavailableReasons, reason) }
			if v.NodeName != node.GetName() || v.NodeUID != uid || v.Name == "" || names[v.Name] != 1 {
				reject("incomplete_identity")
			}
			if doc.CollectedAt.IsZero() || doc.CollectedAt.After(now) || now.Sub(doc.CollectedAt) > time.Minute {
				reject("stale_facts")
			}
			if !nodeReady(&node) || node.GetAnnotations()["networking.kubercloud.com/chassis"] == "" || !switchDaemonReady(node.GetName(), daemons) {
				reject("node_or_daemon_not_ready")
			}
			if membershipErr != nil {
				reject("switch_membership_changed")
			}
			if node.GetLabels()[allocatedNodeLabel] != "true" {
				reject("node_not_allocated")
			}
			if sw.BindingState == "degraded" || (sw.ClusterID != "" && !owned) {
				reject("switch_group_not_owned")
			}
			if v.Kind != "device" {
				reject("not_physical_device")
			}
			mac, err := net.ParseMAC(v.MAC)
			if err != nil || len(mac) != 6 {
				reject("invalid_mac")
			}
			if !v.LinkUp || !v.Carrier {
				reject("link_not_ready")
			}
			if v.Management || v.DefaultRoute {
				reject("management_or_default_route")
			}
			for _, raw := range v.Addresses {
				ip, err := netip.ParsePrefix(raw)
				if err != nil || !ip.Addr().IsLinkLocalUnicast() {
					reject("address_assigned")
					break
				}
			}
			if (v.OVSManaged || v.Master != "") && (!owned || !switchWiring(&node, v, sw.GroupName)) {
				reject("already_in_use")
			}
			if config == nil || config.GetUID() == "" || config.GetDeletionTimestamp() != nil {
				reject("provider_config_unavailable")
			} else {
				v.KCConfigUID = string(config.GetUID())
				v.KCDeviceOwner = config.GetAnnotations()[adoptionMarker(v.Name)]
				if v.KCDeviceOwner != "" {
					reject("already_registered")
				}
			}
			for _, vlan := range vlans {
				if crString(&vlan, "spec", "devName") != v.Name {
					continue
				}
				if !owned || crString(&vlan, "spec", "vClusterName") != sw.GroupName || vlan.GetLabels()[ownerLabel] != "ani-network-service" || vlan.GetLabels()[resourceLabel] == "" {
					reject("provider_vlan_in_use")
				} else {
					v.VlanNetworkIDs = append(v.VlanNetworkIDs, vlan.GetLabels()[resourceLabel])
				}
			}
			slices.Sort(v.UnavailableReasons)
			v.UnavailableReasons = slices.Compact(v.UnavailableReasons)
			slices.Sort(v.VlanNetworkIDs)
			v.Selectable = len(v.UnavailableReasons) == 0
			result.Items = append(result.Items, v)
		}
	}
	for uid, n := range frozen {
		if !seen[uid] {
			result.Items = append(result.Items, biz.NodeInterface{NodeName: n.NodeName, NodeUID: uid, UnavailableReasons: []string{"frozen_node_missing"}})
		}
	}
	slices.SortFunc(result.Items, func(a, b biz.NodeInterface) int {
		if n := strings.Compare(a.NodeUID, b.NodeUID); n != 0 {
			return n
		}
		return strings.Compare(a.Name, b.Name)
	})
	stable := slices.Clone(result.Items)
	for i := range stable {
		stable[i].ObservedAt = time.Time{}
	}
	result.Fingerprint = contentHash(struct {
		Profile, ClusterID, SwitchID, BindingID string
		Items                                   []biz.NodeInterface
	}{"kcn-v0.9", cluster, sw.ID, sw.BindingID, stable})
	return result
}
