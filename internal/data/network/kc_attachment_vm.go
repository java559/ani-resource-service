package data

import (
	"context"
	"encoding/json"
	"fmt"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func vmPrimarySpecMatches(spec map[string]any, plan *biz.VMPrimaryPlan) bool {
	if plan == nil {
		return false
	}
	networks, _, _ := unstructured.NestedSlice(spec, "networks")
	interfaces, _, _ := unstructured.NestedSlice(spec, "domain", "devices", "interfaces")
	if len(networks) != 1 || len(interfaces) != 1 {
		return false
	}
	n, ok := networks[0].(map[string]any)
	if !ok {
		return false
	}
	i, ok := interfaces[0].(map[string]any)
	if !ok {
		return false
	}
	def, _, _ := unstructured.NestedBool(n, "multus", "default")
	nad, _, _ := unstructured.NestedString(n, "multus", "networkName")
	_, bridge := i["bridge"].(map[string]any)
	return n["name"] == "default" && i["name"] == "default" && def && nad == plan.Namespace+"/"+plan.NADName && bridge
}

func vmLauncherMainNetworkMatches(pod *unstructured.Unstructured, plan *biz.VMPrimaryPlan) bool {
	var status []struct {
		Name      string `json:"name"`
		Default   bool   `json:"default"`
		Interface string `json:"interface"`
	}
	if plan == nil || json.Unmarshal([]byte(pod.GetAnnotations()["k8s.v1.cni.cncf.io/network-status"]), &status) != nil {
		return false
	}
	defaults := 0
	for _, item := range status {
		if !item.Default {
			continue
		}
		if kcRef(item.Name, pod.GetNamespace()) != plan.Namespace+"/"+plan.NADName || item.Interface != "eth0" {
			return false
		}
		defaults++
	}
	return defaults == 1
}

func (p *KCProvider) verifyVMPod(ctx context.Context, w biz.AttachmentWork, pod *unstructured.Unstructured, track func(attachmentRelation)) (bool, bool, error) {
	conflict := func() (bool, bool, error) { return false, false, &biz.ProviderError{Kind: biz.ProviderConflict} }
	if pod.GetLabels()["kubevirt.io"] != "virt-launcher" {
		return conflict()
	}
	vmiRef, ok := workloadController(pod, "kubevirt.io/v1", "VirtualMachineInstance")
	if !ok {
		return conflict()
	}
	c := w.ConsumerSnapshot
	current, retired := false, false
	identity := workloadIdentity(pod)
	if c != nil {
		for _, item := range c.CurrentPods {
			if item.Reference.UID == identity.UID {
				if item.Reference != identity || item.Owner != vmiRef {
					return conflict()
				}
				current = true
			}
		}
		for _, item := range c.RetiredPods {
			if item.Reference.UID == identity.UID {
				if item.Reference != identity || item.Owner != vmiRef {
					return conflict()
				}
				retired = true
			}
		}
	}
	vmi, err := p.getWorkload(ctx, vmiRef)
	if apierrors.IsNotFound(err) && (retired || (c != nil && c.State != "open")) {
		return false, false, nil
	}
	if err != nil {
		return false, false, readFailure(err)
	}
	if workloadIdentity(vmi) != vmiRef {
		// A retired launcher retains its old VMI UID across native Restart.
		// A current launcher is never rebound to a same-name replacement.
		if retired {
			return false, false, nil
		}
		return conflict()
	}
	rootRef, ok := workloadController(vmi, "kubevirt.io/v1", "VirtualMachine")
	if !ok {
		return conflict()
	}
	root, err := p.getWorkload(ctx, rootRef)
	if apierrors.IsNotFound(err) && c != nil && c.State != "open" {
		return false, false, nil
	}
	if err != nil {
		return false, false, readFailure(err)
	}
	if workloadIdentity(root) != rootRef {
		return conflict()
	}
	labels := map[string]string{tenantLabel: w.Attachment.TenantID, instanceLabel: w.Attachment.InstanceID, attachmentLabel: w.Attachment.ID, submissionLabel: w.Attachment.SubmissionID, generationLabel: fmt.Sprint(w.Attachment.Generation)}
	template, _, _ := unstructured.NestedMap(root.Object, "spec", "template")
	templateLabels, _, _ := unstructured.NestedStringMap(template, "metadata", "labels")
	for key, value := range labels {
		if root.GetLabels()[key] != value || templateLabels[key] != value || vmi.GetLabels()[key] != value {
			return conflict()
		}
	}
	annotation, _, _ := unstructured.NestedString(template, "metadata", "annotations", subnetAnnotation)
	if annotation != w.Plan.PrimaryNetworkRef || vmi.GetAnnotations()[subnetAnnotation] != w.Plan.PrimaryNetworkRef {
		return conflict()
	}
	rootSpec, _, _ := unstructured.NestedMap(template, "spec")
	vmiSpec, _, _ := unstructured.NestedMap(vmi.Object, "spec")
	if !vmPrimarySpecMatches(rootSpec, w.Plan.VMPrimary) || !vmPrimarySpecMatches(vmiSpec, w.Plan.VMPrimary) {
		return conflict()
	}
	if c != nil && c.RootController.UID != "" && c.RootController != rootRef {
		return conflict()
	}
	phase, _, _ := unstructured.NestedString(vmi.Object, "status", "phase")
	address := ""
	if interfaces, _, _ := unstructured.NestedSlice(vmi.Object, "status", "interfaces"); len(interfaces) > 0 {
		for _, value := range interfaces {
			item, ok := value.(map[string]any)
			if ok && item["name"] == "default" {
				address, _, _ = unstructured.NestedString(item, "ipAddress")
			}
		}
	}
	track(attachmentRelation{Kind: "VirtualMachine", Namespace: rootRef.Namespace, Name: rootRef.Name, UID: rootRef.UID, Current: root.GetDeletionTimestamp() == nil})
	track(attachmentRelation{Kind: "VirtualMachineInstance", Namespace: vmiRef.Namespace, Name: vmiRef.Name, UID: vmiRef.UID, OwnerUID: rootRef.UID, Address: address, Current: current && phase == "Running" && vmi.GetDeletionTimestamp() == nil})
	if !vmLauncherMainNetworkMatches(pod, w.Plan.VMPrimary) {
		podPhase, _, _ := unstructured.NestedString(pod.Object, "status", "phase")
		// Multus records network-status only after CNI ADD succeeds. The exact
		// owner/spec/default-NAD chain can be known while that callback is still
		// pending; absence of feedback is not a contradictory owner relation.
		if podPhase == "Pending" && pod.GetAnnotations()["k8s.v1.cni.cncf.io/network-status"] == "" && pod.GetAnnotations()["v1.multus-cni.io/default-network"] == w.Plan.VMPrimary.Namespace+"/"+w.Plan.VMPrimary.NADName {
			return false, true, nil
		}
		return conflict()
	}
	if c == nil || c.RootController.UID == "" || (!current && !retired) {
		return false, true, nil
	}
	return current && c.State == "open" && c.DesiredRunState == "running" && phase == "Running" && root.GetDeletionTimestamp() == nil && vmi.GetDeletionTimestamp() == nil && pod.GetDeletionTimestamp() == nil, false, nil
}
