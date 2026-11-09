package vm

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
)

// Clone APIs are pinned to the served storage version measured on the target
// cluster (v1beta1). A different served version is reported as a dependency.
var cloneResource = schema.GroupVersionResource{Group: "clone.kubevirt.io", Version: "v1beta1", Resource: "virtualmachineclones"}
var storageClassResource = schema.GroupVersionResource{Group: "storage.k8s.io", Version: "v1", Resource: "storageclasses"}

// CloneCreateOperationAnnotation anchors an uncertain create receipt to this
// operation. It is written only on the Clone object.
const CloneCreateOperationAnnotation = "vm.ani.io/clone-create-operation-id"

// cloneSourceAnnotationKeys are source identity markers that must never stay on
// a cloned target volume. Native lifecycle markers (restore.kubevirt.io/*,
// cdi.kubevirt.io/*) are preserved on purpose.
var cloneSourceAnnotationKeys = []string{
	"vm.ani.io/vm-id", "vm.ani.io/create-digest", "vm.ani.io/media-id",
	"vm.ani.io/media-source-digest", "vm.ani.io/initialization-intent",
	"vm.ani.io/network-initialization", "vm.ani.io/snapshot-id",
	"vm.ani.io/snapshot-uid", "vm.ani.io/source-vm-uid", "vm.ani.io/source-sha256",
	"network.ani.io/instance-id", "network.ani.io/attachment-id",
	"network.ani.io/submission-id", "network.ani.io/generation",
	"network.ani.io/interface-revision", "networking.kubercloud.com/vnic",
	"kubectl.kubernetes.io/last-applied-configuration",
}

func cloneUnsupported(volume, reason string) biz.CloneBlocker {
	return biz.CloneBlocker{Kind: "UNSUPPORTED", VolumeName: volume, Reason: reason}
}

func cloneNotReady(volume, reason string) biz.CloneBlocker {
	return biz.CloneBlocker{Kind: "NOT_READY", VolumeName: volume, Reason: reason}
}

// ObserveSourceCloneEligibility reads the accepted source native VM and checks
// the first-phase source scope plus the PVC/DV snapshot capability. Only PVC and
// DataVolume volumes are classified: a platform NoCloud seed is reported as
// not-snapshot-capable by KubeVirt and must not reject a normal Linux source.
func (n *Native) ObserveSourceCloneEligibility(ctx context.Context, v biz.VM) (biz.CloneEligibility, error) {
	var result biz.CloneEligibility
	c := v.Spec.CloneSource
	if c == nil || c.Native.Name == "" || c.Native.UID == "" {
		return result, biz.ErrInvalid
	}
	src, err := n.client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, c.Native.Name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		result.Unsupported = append(result.Unsupported, cloneUnsupported("", "source VM is gone"))
		return result, nil
	}
	if err != nil {
		return result, err
	}
	if string(src.GetUID()) != c.Native.UID || src.GetLabels()["vm.ani.io/vm-id"] != c.SourceVMID || src.GetNamespace() != v.Spec.Namespace {
		return result, biz.ErrOwnership
	}
	if dvt, found, _ := unstructured.NestedSlice(src.Object, "spec", "dataVolumeTemplates"); found && len(dvt) > 0 {
		result.Unsupported = append(result.Unsupported, cloneUnsupported("", "source uses dataVolumeTemplates"))
	}
	volumes, _, _ := unstructured.NestedSlice(src.Object, "spec", "template", "spec", "volumes")
	claims := map[string]string{}
	plan := map[string]bool{}
	for _, name := range v.FrozenVolumeNames() {
		plan[name] = true
	}
	for _, raw := range volumes {
		vol, ok := raw.(map[string]any)
		if !ok {
			return result, biz.ErrChanged
		}
		name, _ := vol["name"].(string)
		if name == "" {
			return result, biz.ErrChanged
		}
		if pvc, ok := vol["persistentVolumeClaim"].(map[string]any); ok {
			claim, _ := pvc["claimName"].(string)
			if claim == "" {
				result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "volume has no persistentVolumeClaim.claimName"))
				continue
			}
			claims[name] = claim
			continue
		}
		if dv, ok := vol["dataVolume"].(map[string]any); ok {
			dvName, _ := dv["name"].(string)
			if dvName == "" {
				result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "volume has no dataVolume.name"))
				continue
			}
			claims[name] = dvName
			continue
		}
		if name == "guest-initialization" {
			ref, _, _ := unstructured.NestedString(vol, "cloudInitNoCloud", "secretRef", "name")
			netRef, _, _ := unstructured.NestedString(vol, "cloudInitNoCloud", "networkDataSecretRef", "name")
			if ref != "vm-init-"+c.SourceVMID || netRef != ref {
				result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "source uses a non-standard initialization secret"))
			}
			continue
		}
		if strings.HasPrefix(name, "media-install") || strings.HasPrefix(name, "media-driver") {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "source still mounts installation media"))
			continue
		}
		result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "unsupported source volume type"))
	}
	for name := range claims {
		if !plan[name] {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "source volume is not part of the frozen disk plan"))
		}
	}
	for name := range plan {
		if _, ok := claims[name]; !ok {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(name, "frozen disk volume is absent from the source template"))
		}
	}
	// Media devices and multi-NIC/manual VNIC sources stay out of phase 1.
	disks, _, _ := unstructured.NestedSlice(src.Object, "spec", "template", "spec", "domain", "devices", "disks")
	for _, raw := range disks {
		dev, ok := raw.(map[string]any)
		if !ok {
			return result, biz.ErrChanged
		}
		if _, ok := dev["cdrom"]; ok {
			result.Unsupported = append(result.Unsupported, cloneUnsupported("", "source still boots from media"))
			break
		}
	}
	interfaces, _, _ := unstructured.NestedSlice(src.Object, "spec", "template", "spec", "domain", "devices", "interfaces")
	networks, _, _ := unstructured.NestedSlice(src.Object, "spec", "template", "spec", "networks")
	if len(interfaces) > 1 || len(networks) > 1 {
		result.Unsupported = append(result.Unsupported, cloneUnsupported("", "source uses multiple network interfaces"))
	}
	annotations := src.GetAnnotations()
	if annotations["networking.kubercloud.com/vnic"] != "" || annotations["kubevirt.io/persistent-state-for"] != "" {
		result.Unsupported = append(result.Unsupported, cloneUnsupported("", "source uses a manual VNIC or backend storage"))
	}
	if pstate := src.GetLabels()["persistent-state-for"]; pstate != "" {
		result.Unsupported = append(result.Unsupported, cloneUnsupported("", "source uses backend storage"))
	}
	if len(result.Unsupported) > 0 {
		return result, nil
	}
	// Field-level capability. KubeVirt reports one entry per volume it can
	// snapshot; a missing entry is "not reported yet", not "supported".
	statuses, _, _ := unstructured.NestedSlice(src.Object, "status", "volumeSnapshotStatuses")
	byVolume := map[string]map[string]any{}
	for _, raw := range statuses {
		entry, ok := raw.(map[string]any)
		if !ok {
			return result, biz.ErrChanged
		}
		if name, _ := entry["name"].(string); name != "" {
			byVolume[name] = entry
		}
	}
	scCache := map[string]string{}
	for volume, claim := range claims {
		entry, ok := byVolume[volume]
		if !ok {
			result.NotReady = append(result.NotReady, cloneNotReady(volume, "snapshot status not reported yet"))
			continue
		}
		if enabled, _, _ := unstructured.NestedBool(entry, "enabled"); !enabled {
			reason, _ := entry["reason"].(string)
			if reason == "" {
				reason = "native volume snapshot is disabled"
			}
			result.NotReady = append(result.NotReady, cloneNotReady(volume, reason))
		}
		pvc, err := n.client.Resource(pvcResource).Namespace(v.Spec.Namespace).Get(ctx, claim, metav1.GetOptions{})
		if apierrors.IsNotFound(err) {
			result.NotReady = append(result.NotReady, cloneNotReady(volume, "source PVC is absent"))
			continue
		}
		if err != nil {
			return result, err
		}
		mode, _, _ := unstructured.NestedString(pvc.Object, "spec", "volumeMode")
		if mode != "Block" {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(volume, "source PVC volumeMode is not Block"))
		}
		modes, _, _ := unstructured.NestedStringSlice(pvc.Object, "spec", "accessModes")
		if len(modes) != 1 || modes[0] != "ReadWriteOnce" {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(volume, "source PVC accessModes is not ReadWriteOnce"))
		}
		if phase, _, _ := unstructured.NestedString(pvc.Object, "status", "phase"); phase != "Bound" {
			result.NotReady = append(result.NotReady, cloneNotReady(volume, "source PVC is not Bound"))
		}
		class, _, _ := unstructured.NestedString(pvc.Object, "spec", "storageClassName")
		if class == "" {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(volume, "source PVC has no StorageClass"))
			continue
		}
		binding, ok := scCache[class]
		if !ok {
			sc, err := n.client.Resource(storageClassResource).Get(ctx, class, metav1.GetOptions{})
			if apierrors.IsNotFound(err) {
				result.NotReady = append(result.NotReady, cloneNotReady(volume, "source StorageClass is absent"))
				continue
			}
			if err != nil {
				return result, err
			}
			binding, _, _ = unstructured.NestedString(sc.Object, "volumeBindingMode")
			if binding == "" {
				binding = "Immediate"
			}
			scCache[class] = binding
		}
		if binding != "Immediate" {
			result.Unsupported = append(result.Unsupported, cloneUnsupported(volume, "source StorageClass is not Immediate"))
		}
	}
	return result, nil
}

// newCloneObject builds the fixed clone submission. The four metadata filter
// groups, both explicit typed references and the absence of spec.patches are part
// of the contract and are asserted by tests.
func newCloneObject(v biz.VM, r biz.CloneRequest) *unstructured.Unstructured {
	object := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "clone.kubevirt.io/v1beta1",
		"kind":       "VirtualMachineClone",
		"metadata":   map[string]any{"name": r.Name, "namespace": v.Spec.Namespace},
		"spec": map[string]any{
			"source":            map[string]any{"apiGroup": "kubevirt.io", "kind": "VirtualMachine", "name": r.Source.Name},
			"target":            map[string]any{"apiGroup": "kubevirt.io", "kind": "VirtualMachine", "name": r.TargetName},
			"volumeNamePolicy":  "RandomizeNames",
			"newSMBiosSerial":   r.SMBiosSerial,
			"labelFilters":      cloneFilters,
			"annotationFilters": cloneFilters,
			"template": map[string]any{
				"labelFilters":      cloneFilters,
				"annotationFilters": cloneFilters,
			},
		},
	}}
	object.SetLabels(labelVM(v))
	object.SetAnnotations(map[string]string{CloneCreateOperationAnnotation: r.OperationID})
	return object
}

// EnsureClone creates the fixed-name clone or adopts the same object by its
// recorded identity. A same-name object with another UID is never adopted.
func (n *Native) EnsureClone(ctx context.Context, v biz.VM, r biz.CloneRequest) (biz.CloneObservation, error) {
	existing, err := n.client.Resource(cloneResource).Namespace(v.Spec.Namespace).Get(ctx, r.Name, metav1.GetOptions{})
	if err == nil {
		return n.projectClone(ctx, v, r, existing)
	}
	if !apierrors.IsNotFound(err) {
		return biz.CloneObservation{}, err
	}
	object := newCloneObject(v, r)
	created, err := n.client.Resource(cloneResource).Namespace(v.Spec.Namespace).Create(ctx, object, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(err) {
		return n.EnsureClone(ctx, v, r)
	}
	if apierrors.IsInvalid(err) || apierrors.IsBadRequest(err) || apierrors.IsForbidden(err) {
		// A definite admission rejection cannot have created the object, so the
		// worker may close this create without waiting for an unknown receipt.
		return biz.CloneObservation{}, fmt.Errorf("%w: %v", biz.ErrInvalid, err)
	}
	if err != nil {
		return biz.CloneObservation{}, err
	}
	return n.projectClone(ctx, v, r, created)
}

// cloneFilters keeps every unlisted metadata key and drops exactly the known
// source identity keys. The same array is used for all four metadata groups.
var cloneFilters = []any{
	"*",
	"!^vm[.]ani[.]io/vm-id$",
	"!^vm[.]ani[.]io/create-digest$",
	"!^vm[.]ani[.]io/clone-create-operation-id$",
	"!^network[.]ani[.]io/instance-id$",
	"!^network[.]ani[.]io/attachment-id$",
	"!^network[.]ani[.]io/submission-id$",
	"!^network[.]ani[.]io/generation$",
	"!^network[.]ani[.]io/interface-revision$",
	"!^networking[.]kubercloud[.]com/vnic$",
	"!^kubectl[.]kubernetes[.]io/last-applied-configuration$",
	"!^vm[.]ani[.]io/media-id$",
	"!^vm[.]ani[.]io/media-source-digest$",
	"!^vm[.]ani[.]io/initialization-intent$",
	"!^vm[.]ani[.]io/network-initialization$",
	"!^vm[.]ani[.]io/snapshot-id$",
	"!^vm[.]ani[.]io/snapshot-uid$",
	"!^vm[.]ani[.]io/source-vm-uid$",
	"!^vm[.]ani[.]io/source-sha256$",
	"!^verification[.]ani[.]io/run-id$",
	"!^verification[.]ani[.]io/preparation-method$",
	"!^verification[.]ani[.]io/origin-vm-uid$",
}

func (n *Native) ObserveClone(ctx context.Context, v biz.VM, r biz.CloneRequest) (biz.CloneObservation, error) {
	object, err := n.client.Resource(cloneResource).Namespace(v.Spec.Namespace).Get(ctx, r.Name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return biz.CloneObservation{Exists: false}, nil
	}
	if err != nil {
		return biz.CloneObservation{}, err
	}
	return n.projectClone(ctx, v, r, object)
}

// projectClone verifies ordinary ownership before exposing any state.
func (n *Native) projectClone(ctx context.Context, v biz.VM, r biz.CloneRequest, object *unstructured.Unstructured) (biz.CloneObservation, error) {
	state := biz.CloneObservation{Exists: true, UID: string(object.GetUID()), ResourceVersion: object.GetResourceVersion()}
	if object.GetLabels()["vm.ani.io/vm-id"] != v.ID || object.GetAnnotations()[CloneCreateOperationAnnotation] != r.OperationID {
		return state, biz.ErrOwnership
	}
	sourceName, _, _ := unstructured.NestedString(object.Object, "spec", "source", "name")
	targetName, _, _ := unstructured.NestedString(object.Object, "spec", "target", "name")
	if sourceName != r.Source.Name || targetName != r.TargetName {
		return state, biz.ErrOwnership
	}
	state.Phase, _, _ = unstructured.NestedString(object.Object, "status", "phase")
	state.SnapshotName, _, _ = unstructured.NestedString(object.Object, "status", "snapshotName")
	state.RestoreName, _, _ = unstructured.NestedString(object.Object, "status", "restoreName")
	conditions, _, _ := unstructured.NestedSlice(object.Object, "status", "conditions")
	for _, raw := range conditions {
		condition, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		kind, _ := condition["type"].(string)
		status, _ := condition["status"].(string)
		reason, _ := condition["reason"].(string)
		message, _ := condition["message"].(string)
		if kind == "Progressing" && status == "False" && reason == "Pending" {
			state.Pending = true
			if message != "" {
				state.Reason = message
			} else {
				state.Reason = reason
			}
		}
		if kind == "Ready" && status != "True" && message != "" && state.Reason == "" {
			state.Reason = message
		}
	}
	switch {
	case state.Phase == "Succeeded":
		state.Succeeded = true
	case state.Phase == "Failed":
		state.Failed = true
		if state.Reason == "" {
			state.Reason = "clone failed"
		}
	}
	state.Target = biz.ObjectRef{Kind: "VirtualMachine", Namespace: v.Spec.Namespace, Name: targetName}
	if target, err := n.client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, targetName, metav1.GetOptions{}); err == nil {
		state.Target = objectRef(target)
	} else if !apierrors.IsNotFound(err) {
		return state, err
	}
	if state.RestoreName != "" {
		outputs, err := n.cloneRestoreOutputs(ctx, v, state.RestoreName)
		if err != nil {
			return state, err
		}
		state.Outputs = outputs
	}
	return state, nil
}

// cloneRestoreOutputs reads the named outputs of the clone's own Restore. The
// names are a cancellation hand-off, not a copy-success proof.
func (n *Native) cloneRestoreOutputs(ctx context.Context, v biz.VM, name string) ([]biz.VolumeRef, error) {
	restore, err := n.client.Resource(restoreResource).Namespace(v.Spec.Namespace).Get(ctx, name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	entries, _, _ := unstructured.NestedSlice(restore.Object, "status", "restores")
	var outputs []biz.VolumeRef
	for _, raw := range entries {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		volume, _ := entry["volumeName"].(string)
		claim, _ := entry["persistentVolumeClaim"].(string)
		if volume == "" {
			continue
		}
		ref := biz.VolumeRef{VolumeName: volume, PVCName: claim}
		if claim != "" {
			if pvc, err := n.client.Resource(pvcResource).Namespace(v.Spec.Namespace).Get(ctx, claim, metav1.GetOptions{}); err == nil {
				ref.PVCUID = string(pvc.GetUID())
			} else if !apierrors.IsNotFound(err) {
				return nil, err
			}
		}
		outputs = append(outputs, ref)
	}
	return outputs, nil
}

// DeleteClone deletes the adopted clone by UID with foreground propagation and
// reports whether the object is gone.
func (n *Native) DeleteClone(ctx context.Context, v biz.VM, r biz.CloneRequest) (bool, error) {
	uid, err := n.cloneUID(ctx, v.Spec.Namespace, r.Name)
	if err != nil {
		return false, err
	}
	if uid == "" {
		return true, nil
	}
	precondition := types.UID(uid)
	policy := metav1.DeletePropagationForeground
	err = n.client.Resource(cloneResource).Namespace(v.Spec.Namespace).Delete(ctx, r.Name, metav1.DeleteOptions{Preconditions: &metav1.Preconditions{UID: &precondition}, PropagationPolicy: &policy})
	if err != nil && !apierrors.IsNotFound(err) {
		return false, err
	}
	uid, err = n.cloneUID(ctx, v.Spec.Namespace, r.Name)
	if err != nil {
		return false, err
	}
	return uid == "", nil
}

// cloneUID reads the current clone UID. It returns no error for an absent object.
func (n *Native) cloneUID(ctx context.Context, namespace, name string) (string, error) {
	object, err := n.client.Resource(cloneResource).Namespace(namespace).Get(ctx, name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return string(object.GetUID()), nil
}

// ApplyCloneTargetConfiguration adapts the stopped clone target and registers
// the named volumes it actually references. It is idempotent: a replayed call
// with the same registered refs converges to the same patch.
func (n *Native) ApplyCloneTargetConfiguration(ctx context.Context, v biz.VM, r biz.CloneRequest) ([]biz.Disk, biz.ObjectRef, error) {
	target, err := n.client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, r.TargetName, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return nil, biz.ObjectRef{}, biz.ErrDependency
	}
	if err != nil {
		return nil, biz.ObjectRef{}, err
	}
	ref := objectRef(target)
	if v.Native.UID != "" && v.Native.UID != ref.UID {
		return nil, ref, biz.ErrOwnership
	}
	if _, err := n.client.Resource(vmiResource).Namespace(v.Spec.Namespace).Get(ctx, r.TargetName, metav1.GetOptions{}); err == nil {
		return nil, ref, biz.ErrBusy
	} else if !apierrors.IsNotFound(err) {
		return nil, ref, err
	}
	volumes, _, _ := unstructured.NestedSlice(target.Object, "spec", "template", "spec", "volumes")
	devices, _, _ := unstructured.NestedSlice(target.Object, "spec", "template", "spec", "domain", "devices", "disks")
	newVolumes := plainSlice(volumes)
	newDevices := plainSlice(devices)
	// Remove the standard one-shot seed by its actual template name, never by index.
	var keptVolumes []any
	for _, raw := range newVolumes {
		volume, ok := raw.(map[string]any)
		if !ok {
			return nil, ref, biz.ErrChanged
		}
		if name, _ := volume["name"].(string); name == "guest-initialization" {
			continue
		}
		keptVolumes = append(keptVolumes, volume)
	}
	newVolumes = keptVolumes
	var keptDevices []any
	for _, raw := range newDevices {
		device, ok := raw.(map[string]any)
		if !ok {
			return nil, ref, biz.ErrChanged
		}
		if name, _ := device["name"].(string); name == "guest-initialization" {
			continue
		}
		keptDevices = append(keptDevices, device)
	}
	newDevices = keptDevices
	registered := make([]biz.Disk, 0, len(v.Disks))
	for _, disk := range v.Disks {
		if disk.Tombstone || !disk.Attached {
			continue
		}
		_, volume, err := namedItems(newVolumes, disk.VolumeName)
		if err != nil {
			return nil, ref, err
		}
		if volume == nil {
			return nil, ref, biz.ErrChanged
		}
		claim, _, _ := unstructured.NestedString(volume, "persistentVolumeClaim", "claimName")
		if claim == "" {
			return nil, ref, biz.ErrChanged
		}
		if disk.Role == "data" {
			if err = unstructured.SetNestedField(volume, true, "persistentVolumeClaim", "hotpluggable"); err != nil {
				return nil, ref, err
			}
		}
		_, device, err := namedItems(newDevices, disk.VolumeName)
		if err != nil {
			return nil, ref, err
		}
		if device == nil {
			return nil, ref, biz.ErrChanged
		}
		if disk.Role == "data" {
			device["disk"] = map[string]any{"bus": "scsi"}
			delete(device, "lun")
			delete(device, "cdrom")
		}
		device["serial"] = disk.Serial
		pvc, err := n.client.Resource(pvcResource).Namespace(v.Spec.Namespace).Get(ctx, claim, metav1.GetOptions{})
		if apierrors.IsNotFound(err) {
			return nil, ref, biz.ErrDependency
		}
		if err != nil {
			return nil, ref, err
		}
		if mode, _, _ := unstructured.NestedString(pvc.Object, "spec", "volumeMode"); mode != "Block" {
			return nil, ref, biz.ErrOwnership
		}
		if phase, _, _ := unstructured.NestedString(pvc.Object, "status", "phase"); phase != "Bound" {
			return nil, ref, biz.ErrDependency
		}
		if err = n.adoptTargetVolume(ctx, v, ref, pvc); err != nil {
			return nil, ref, err
		}
		capacity := int64(0)
		if size, _, _ := unstructured.NestedString(pvc.Object, "status", "capacity", "storage"); size != "" {
			quantity, err := resource.ParseQuantity(size)
			if err != nil {
				return nil, ref, biz.ErrChanged
			}
			capacity = quantity.Value()
		}
		registeredDisk := disk
		registeredDisk.PVC = biz.ObjectRef{Kind: "PersistentVolumeClaim", Namespace: v.Spec.Namespace, Name: claim, UID: string(pvc.GetUID())}
		registeredDisk.DV = biz.ObjectRef{}
		registeredDisk.ObservedCapacityBytes = capacity
		registeredDisk.Preparation = "Bound"
		registered = append(registered, registeredDisk)
	}
	// Merge target identity into the metadata the filters preserved instead of
	// replacing whole maps, so native restore/PCI/network markers stay intact.
	rawLabels := map[string]any{}
	for k, x := range target.GetLabels() {
		rawLabels[k] = x
	}
	for k, x := range labelVM(v) {
		rawLabels[k] = x
	}
	for k, x := range v.Network.Labels {
		rawLabels[k] = x
	}
	templateAnnotations := map[string]any{}
	if template, ok, _ := unstructured.NestedMap(target.Object, "spec", "template", "metadata", "annotations"); ok {
		for k, x := range template {
			templateAnnotations[k] = x
		}
	}
	if v.Network.KCNSubnetRef != "" {
		templateAnnotations["networking.kubercloud.com/subnet"] = v.Network.KCNSubnetRef
	}
	templateAnnotations[biz.SecurityGroupsAnnotation] = strings.Join(v.SecurityGroupNames, ",")
	if v.Network.BasicIsolationName != "" {
		templateAnnotations["networking.kubercloud.com/basic-network-isolation"] = v.Network.BasicIsolationName
	}
	patch := []map[string]any{
		{"op": "test", "path": "/metadata/uid", "value": ref.UID},
		{"op": "test", "path": "/metadata/resourceVersion", "value": target.GetResourceVersion()},
		{"op": "add", "path": "/metadata/labels", "value": rawLabels},
		{"op": "add", "path": "/spec/template/metadata/labels", "value": rawLabels},
		{"op": "add", "path": "/spec/template/metadata/annotations", "value": templateAnnotations},
		{"op": "add", "path": "/spec/template/spec/volumes", "value": newVolumes},
		{"op": "add", "path": "/spec/template/spec/domain/devices/disks", "value": newDevices},
		{"op": "add", "path": "/spec/template/spec/domain/firmware/uuid", "value": v.ID},
		{"op": "add", "path": "/spec/runStrategy", "value": "Manual"},
	}
	body, err := json.Marshal(patch)
	if err != nil {
		return nil, ref, err
	}
	if _, err = n.client.Resource(vmResource).Namespace(v.Spec.Namespace).Patch(ctx, r.TargetName, types.JSONPatchType, body, metav1.PatchOptions{}); err != nil {
		return nil, ref, err
	}
	return registered, ref, nil
}

// adoptTargetVolume clears source identity from the clone's own PVC and makes the
// target VM its controller owner. Native restore/CDI markers are preserved.
func (n *Native) adoptTargetVolume(ctx context.Context, v biz.VM, target biz.ObjectRef, pvc *unstructured.Unstructured) error {
	owners := pvc.GetOwnerReferences()
	hasTarget := false
	hasOtherController := false
	for _, owner := range owners {
		if owner.Kind == "VirtualMachine" && string(owner.UID) == target.UID {
			hasTarget = true
			continue
		}
		if owner.Controller != nil && *owner.Controller {
			hasOtherController = true
		}
	}
	if !hasTarget && hasOtherController {
		return biz.ErrOwnership
	}
	labels := map[string]any{}
	annotations := map[string]any{}
	for _, key := range cloneSourceAnnotationKeys {
		labels[key] = nil
		annotations[key] = nil
	}
	targetLabels := labelVM(v)
	for key, value := range targetLabels {
		labels[key] = value
	}
	patch := map[string]any{"metadata": map[string]any{"labels": labels, "annotations": annotations}}
	if !hasTarget {
		yes := true
		patch["metadata"].(map[string]any)["ownerReferences"] = []metav1.OwnerReference{{APIVersion: "kubevirt.io/v1", Kind: "VirtualMachine", Name: target.Name, UID: types.UID(target.UID), Controller: &yes, BlockOwnerDeletion: &yes}}
	}
	body, err := json.Marshal(patch)
	if err != nil {
		return err
	}
	if _, err = n.client.Resource(pvcResource).Namespace(v.Spec.Namespace).Patch(ctx, pvc.GetName(), types.MergePatchType, body, metav1.PatchOptions{}); err != nil {
		if apierrors.IsConflict(err) {
			return biz.ErrChanged
		}
		return err
	}
	return nil
}

// ClonePresence is the read-only residual check used by the controlled release
// command. It never writes and never enumerates child objects.
type ClonePresence struct {
	Exists          bool
	UID             string
	ResourceVersion string
	Deleting        bool
	Finalizers      []string
}

// ClonePresence reads one clone object by its fixed name.
func (n *Native) ClonePresence(ctx context.Context, namespace, name string) (ClonePresence, error) {
	object, err := n.client.Resource(cloneResource).Namespace(namespace).Get(ctx, name, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return ClonePresence{}, nil
	}
	if err != nil {
		return ClonePresence{}, err
	}
	return ClonePresence{Exists: true, UID: string(object.GetUID()), ResourceVersion: object.GetResourceVersion(), Deleting: object.GetDeletionTimestamp() != nil, Finalizers: object.GetFinalizers()}, nil
}

var _ biz.CloneNativePort = (*Native)(nil)

// cloneSummary keeps a stable diagnostic string for the worker reason field.
func cloneSummary(state biz.CloneObservation) string {
	if state.Phase != "" {
		return fmt.Sprintf("clone %s", state.Phase)
	}
	if state.Reason != "" {
		return state.Reason
	}
	return "clone pending"
}
