package data

import (
	"context"
	"encoding/json"
	"net/netip"
	"slices"
	"strings"
	"time"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
)

func retirementMarker(device string) string {
	return "network.ani.io/device-retirement-" + contentHash(device)[:16]
}

func (p *KCProvider) InspectLegacyDeviceRetirement(ctx context.Context, w biz.LegacyDeviceRetirementWork) (biz.LegacyDeviceRetirementObservation, error) {
	var result biz.LegacyDeviceRetirementObservation
	if w.ClusterID != p.repository.placement.ClusterID || w.BindingID == "" || w.DeviceName == "" {
		return result, switchConflict()
	}
	now, err := p.repository.queries.DatabaseTime(ctx)
	if err != nil {
		return result, readFailure(err)
	}
	result.CollectedAt = now
	blocked := func(reason string) (biz.LegacyDeviceRetirementObservation, error) {
		result.BlockedReason = biz.Reason(reason)
		return result, nil
	}
	if now.Before(w.DrainAfter) {
		return blocked("LEGACY_EXECUTOR_DRAINING")
	}
	if w.Retirement.ConfigUID == "" {
		return blocked("LEGACY_CONFIG_IDENTITY_UNAVAILABLE")
	}
	cm, err := p.client.Resource(kcConfigMaps).Namespace(kcSystemNamespace).Get(ctx, "kcn-config", metav1.GetOptions{})
	if err != nil {
		return result, readFailure(err)
	}
	if string(cm.GetUID()) != w.Retirement.ConfigUID || cm.GetResourceVersion() == "" || cm.GetDeletionTimestamp() != nil {
		return blocked("LEGACY_CONFIG_IDENTITY_CHANGED")
	}
	result.Plan, result.Cleared, result.BlockedReason = legacyRetirementConfigPlan(w, cm)
	if result.BlockedReason != "" {
		return result, nil
	}
	nodes, maps, vlans, err := p.readFreshRetirementFacts(ctx)
	if err != nil {
		return result, err
	}
	// Re-read database time after the collection: a collector may finish while
	// these requests run. Its original timestamps are still checked, not reset.
	now, err = p.repository.queries.DatabaseTime(ctx)
	if err != nil {
		return result, readFailure(err)
	}
	if reason := legacyRetirementReferences(w, nodes, maps, vlans, now); reason != "" {
		return blocked(string(reason))
	}
	result.CollectedAt = now
	result.Hash = contentHash(struct {
		Plan               biz.LegacyDeviceRetirementPlan
		Nodes, Maps, Vlans []unstructured.Unstructured
	}{result.Plan, nodes, maps, vlans})
	return result, nil
}

// Retirement is a separate operation, so it cannot borrow an old resource's
// observation notification fence. Its destructive gate reads every dependency
// directly instead of treating a cached absence as proof of release.
func (p *KCProvider) readFreshRetirementFacts(ctx context.Context) ([]unstructured.Unstructured, []unstructured.Unstructured, []unstructured.Unstructured, error) {
	nodes, err := p.listAll(ctx, kcNodes)
	if err != nil {
		return nil, nil, nil, err
	}
	maps, err := p.client.Resource(kcConfigMaps).Namespace(kcSystemNamespace).List(ctx, metav1.ListOptions{LabelSelector: ownerLabel + "=" + factsOwner})
	if err != nil {
		return nil, nil, nil, readFailure(err)
	}
	vlans, err := p.listAll(ctx, kcVlans)
	if err != nil {
		return nil, nil, nil, err
	}
	return nodes, maps.Items, vlans, nil
}

func legacyRetirementConfigPlan(w biz.LegacyDeviceRetirementWork, cm *unstructured.Unstructured) (biz.LegacyDeviceRetirementPlan, bool, biz.Reason) {
	marker, present := cm.GetAnnotations()[adoptionMarker(w.DeviceName)]
	raw := crString(cm, "data", "managedDevices")
	plan := biz.LegacyDeviceRetirementPlan{ConfigUID: string(cm.GetUID()), ConfigRV: cm.GetResourceVersion(), ManagedRaw: raw, DesiredManagedRaw: raw, MarkerPresent: present, MarkerValue: marker}
	owner, retirementPresent := cm.GetAnnotations()[retirementMarker(w.DeviceName)]
	if retirementPresent && owner != w.Retirement.OperationID {
		return plan, false, "LEGACY_RETIREMENT_OWNER_CONFLICT"
	}
	if present && marker != w.BindingID {
		return plan, false, "LEGACY_ADOPTION_OWNER_CONFLICT"
	}
	parts := strings.Split(raw, ",")
	count := 0
	for _, part := range parts {
		if strings.TrimSpace(part) == w.DeviceName {
			count++
		}
	}
	if count > 1 {
		return plan, false, "LEGACY_MANAGED_DEVICE_AMBIGUOUS"
	}
	managed := count == 1
	if retirementPresent {
		if present || managed != w.PreManaged {
			return plan, false, "LEGACY_RETIRED_EFFECT_DRIFT"
		}
		return plan, true, ""
	}
	if (present && !managed) || (!present && managed != w.PreManaged) {
		return plan, false, "LEGACY_ADOPTION_RESULT_UNKNOWN"
	}
	if !w.PreManaged && present {
		kept := []string{}
		for _, part := range parts {
			if strings.TrimSpace(part) != w.DeviceName {
				kept = append(kept, part)
			}
		}
		plan.DesiredManagedRaw = strings.Join(kept, ",")
	}
	return plan, false, ""
}

func legacyRetirementReferences(w biz.LegacyDeviceRetirementWork, nodes, maps, vlans []unstructured.Unstructured, now time.Time) biz.Reason {
	for _, v := range vlans {
		if crString(&v, "spec", "devName") == w.DeviceName {
			return "LEGACY_NATIVE_VLAN_IN_USE"
		}
	}
	frozen := map[string]biz.NodeInterface{}
	for _, v := range w.Nodes {
		if v.NodeUID == "" || frozen[v.NodeUID].NodeUID != "" {
			return "LEGACY_NODE_IDENTITY_INVALID"
		}
		frozen[v.NodeUID] = v
	}
	seen := map[string]bool{}
	for _, n := range nodes {
		for key, value := range n.GetAnnotations() {
			if strings.HasPrefix(key, virtualSwitchGroupPrefix) && slices.Contains(strings.FieldsFunc(value, func(r rune) bool { return r == ',' || r == ' ' }), w.DeviceName) {
				return "LEGACY_GROUP_DEVICE_IN_USE"
			}
		}
		if n.GetLabels()[allocatedNodeLabel] != "true" {
			continue
		}
		uid := string(n.GetUID())
		expected, ok := frozen[uid]
		if !ok || expected.NodeName != n.GetName() || n.GetDeletionTimestamp() != nil {
			return "LEGACY_NODE_IDENTITY_CHANGED"
		}
		seen[uid] = true
		sources := 0
		for _, cm := range maps {
			if cm.GetNamespace() != kcSystemNamespace || cm.GetLabels()[ownerLabel] != factsOwner || cm.GetLabels()[factsNodeLabel] != uid {
				continue
			}
			sources++
			var doc NodeFactsDocument
			if cm.GetUID() == "" || cm.GetResourceVersion() == "" || cm.GetDeletionTimestamp() != nil || json.Unmarshal([]byte(crString(&cm, "data", "facts.json")), &doc) != nil || doc.Version != 1 || doc.NodeName != expected.NodeName || doc.NodeUID != uid || doc.CollectedAt.IsZero() || doc.CollectedAt.After(now) || now.Sub(doc.CollectedAt) > time.Minute || !doc.OVSBridgeMappingsObserved {
				return "LEGACY_NODE_FACTS_UNAVAILABLE"
			}
			for network := range doc.OVSBridgeMappings {
				if strings.HasSuffix(network, "."+w.DeviceName) {
					return "LEGACY_DEVICE_MAPPING_IN_USE"
				}
			}
			interfaces := 0
			for _, v := range doc.Interfaces {
				if v.Name != w.DeviceName {
					continue
				}
				interfaces++
				if v.NodeUID != uid || v.NodeName != expected.NodeName || v.MAC != expected.MAC || v.Kind != "device" {
					return "LEGACY_DEVICE_IDENTITY_CHANGED"
				}
				if v.OVSManaged || v.Master != "" || len(v.KCBridgeNetworks) > 0 || v.Management || v.DefaultRoute {
					return "LEGACY_DEVICE_PHYSICAL_IN_USE"
				}
				for _, raw := range v.Addresses {
					ip, err := netip.ParsePrefix(raw)
					if err != nil || !ip.Addr().IsLinkLocalUnicast() {
						return "LEGACY_DEVICE_ADDRESS_IN_USE"
					}
				}
			}
			if interfaces != 1 {
				return "LEGACY_DEVICE_FACTS_INCOMPLETE"
			}
		}
		if sources != 1 {
			return "LEGACY_NODE_FACTS_UNAVAILABLE"
		}
	}
	if len(frozen) == 0 || len(seen) != len(frozen) {
		return "LEGACY_NODE_IDENTITY_CHANGED"
	}
	return ""
}

func (p *KCProvider) ClearLegacyDeviceRetirement(ctx context.Context, w biz.LegacyDeviceRetirementWork) error {
	if !w.Prepared {
		return switchConflict()
	}
	current, err := p.InspectLegacyDeviceRetirement(ctx, w)
	if err != nil {
		return err
	}
	if current.BlockedReason != "" {
		return &biz.ProviderError{Kind: biz.ProviderInUse}
	}
	if current.Cleared {
		return nil
	}
	if current.Plan != w.Plan {
		return switchConflict()
	}
	endpoint := p.client.Resource(kcConfigMaps).Namespace(kcSystemNamespace)
	cm, err := endpoint.Get(ctx, "kcn-config", metav1.GetOptions{})
	if err != nil {
		return readFailure(err)
	}
	if string(cm.GetUID()) != w.Plan.ConfigUID || cm.GetResourceVersion() != w.Plan.ConfigRV {
		return switchConflict()
	}
	// The final SQL fence follows all reads of the ConfigMap and the references.
	// A late legacy request uses an older RV or fails its own closing check.
	if err = p.repository.AssertLegacyDeviceRetirement(ctx, w); err != nil {
		return err
	}
	ops := []map[string]any{{"op": "test", "path": "/metadata/uid", "value": w.Plan.ConfigUID}, {"op": "test", "path": "/metadata/resourceVersion", "value": w.Plan.ConfigRV}}
	if w.Plan.ManagedRaw != w.Plan.DesiredManagedRaw {
		ops = append(ops, map[string]any{"op": "test", "path": "/data/managedDevices", "value": w.Plan.ManagedRaw}, map[string]any{"op": "add", "path": "/data/managedDevices", "value": w.Plan.DesiredManagedRaw})
	}
	if w.Plan.MarkerPresent {
		if w.Plan.MarkerValue != w.BindingID {
			return switchConflict()
		}
		key := "/metadata/annotations/" + jsonPointerKey(adoptionMarker(w.DeviceName))
		ops = append(ops, map[string]any{"op": "test", "path": key, "value": w.BindingID}, map[string]any{"op": "remove", "path": key})
	}
	if cm.GetAnnotations() == nil {
		ops = append(ops, map[string]any{"op": "add", "path": "/metadata/annotations", "value": map[string]any{}})
	}
	ops = append(ops, map[string]any{"op": "add", "path": "/metadata/annotations/" + jsonPointerKey(retirementMarker(w.DeviceName)), "value": w.Retirement.OperationID})
	body, _ := json.Marshal(ops)
	updated, err := endpoint.Patch(ctx, "kcn-config", types.JSONPatchType, body, metav1.PatchOptions{FieldManager: "ani-resource-device-retirement"})
	if err != nil {
		return mutationFailure(err)
	}
	_, cleared, reason := legacyRetirementConfigPlan(w, updated)
	if !cleared || reason != "" {
		return &biz.ProviderError{Kind: biz.ProviderUncertain}
	}
	return nil
}
