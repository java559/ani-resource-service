package data

import (
	"testing"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func TestLegacyRetirementConfigCASOnlyRemovesItsOwnEffect(t *testing.T) {
	w := biz.LegacyDeviceRetirementWork{DeviceName: "ens35", BindingID: "binding", Retirement: biz.DeviceRetirement{OperationID: "retire-op"}}
	cm := &unstructured.Unstructured{Object: map[string]any{"metadata": map[string]any{"uid": "config-uid", "resourceVersion": "10", "annotations": map[string]any{adoptionMarker("ens35"): "binding", "foreign": "preserved"}}, "data": map[string]any{"managedDevices": "ens36,ens35,external0"}}}
	p, cleared, reason := legacyRetirementConfigPlan(w, cm)
	if reason != "" || cleared || !p.MarkerPresent || p.MarkerValue != "binding" || p.DesiredManagedRaw != "ens36,external0" {
		t.Fatal(p, cleared, reason)
	}
	w.PreManaged = true
	p, _, reason = legacyRetirementConfigPlan(w, cm)
	if reason != "" || p.DesiredManagedRaw != "ens36,ens35,external0" {
		t.Fatal("preexisting config changed", p, reason)
	}
	annotations := cm.GetAnnotations()
	annotations[adoptionMarker("ens35")] = "another-binding"
	cm.SetAnnotations(annotations)
	if _, _, reason = legacyRetirementConfigPlan(w, cm); reason != "LEGACY_ADOPTION_OWNER_CONFLICT" {
		t.Fatal(reason)
	}
	delete(annotations, adoptionMarker("ens35"))
	cm.SetAnnotations(annotations)
	w.PreManaged = false
	if _, _, reason = legacyRetirementConfigPlan(w, cm); reason != "LEGACY_ADOPTION_RESULT_UNKNOWN" {
		t.Fatal("unowned config was guessed", reason)
	}
	_ = unstructured.SetNestedField(cm.Object, "ens36,external0", "data", "managedDevices")
	annotations[retirementMarker("ens35")] = "retire-op"
	cm.SetAnnotations(annotations)
	if _, cleared, reason = legacyRetirementConfigPlan(w, cm); reason != "" || !cleared {
		t.Fatal("own unknown CAS not resolved", cleared, reason)
	}
	annotations[retirementMarker("ens35")] = "other-operation"
	cm.SetAnnotations(annotations)
	if _, _, reason = legacyRetirementConfigPlan(w, cm); reason != "LEGACY_RETIREMENT_OWNER_CONFLICT" {
		t.Fatal(reason)
	}
}
