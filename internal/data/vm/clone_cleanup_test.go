package vm

import (
	"context"
	"errors"
	"strings"
	"testing"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
)

func cleanupTargetVM(name string, volumes []any, disks []any) *unstructured.Unstructured {
	return &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "kubevirt.io/v1",
		"kind":       "VirtualMachine",
		"metadata":   map[string]any{"name": name, "namespace": "tenant-a", "uid": "target-uid", "resourceVersion": "7"},
		"spec": map[string]any{"template": map[string]any{"spec": map[string]any{
			"volumes": volumes,
			"domain":  map[string]any{"devices": map[string]any{"disks": disks}},
		}}},
	}}
}

func cleanupPVC(name, uid string, owners []any) *unstructured.Unstructured {
	metadata := map[string]any{"name": name, "namespace": "tenant-a", "uid": uid}
	if len(owners) > 0 {
		metadata["ownerReferences"] = owners
	}
	return &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": metadata,
		"spec": map[string]any{"volumeMode": "Block", "accessModes": []any{"ReadWriteOnce"}, "storageClassName": "ani-block"},
	}}
}

func cleanupVM(name string) biz.VM {
	return biz.VM{
		ID: "target-vm", TenantID: "64c5c35a-f554-432c-b4bc-7b4f36f08420",
		Native: biz.ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-a", Name: name, UID: "target-uid"},
		Spec: biz.Spec{Namespace: "tenant-a", Digest: "digest", CloneSource: &biz.CloneSource{
			SourceVMID: classSourceID,
			Native:     biz.ObjectRef{Namespace: "tenant-a", Name: "vm-source", UID: "source-uid"},
			Disks: []biz.CloneDisk{
				{VolumeName: "disk-sys", Role: "system"},
				{VolumeName: "disk-data", Role: "data"},
			},
		}},
	}
}

// TestApplyCloneTargetRejectsUnexpectedPVC pins D6: the adapted target may only
// reference the frozen plan, so an unknown PVC volume fails before any write.
func TestApplyCloneTargetRejectsUnexpectedPVC(t *testing.T) {
	ctx := context.Background()
	volumes := []any{
		map[string]any{"name": "disk-sys", "persistentVolumeClaim": map[string]any{"claimName": "pvc-sys"}},
		map[string]any{"name": "disk-extra", "persistentVolumeClaim": map[string]any{"claimName": "pvc-extra"}},
	}
	disks := []any{
		map[string]any{"name": "disk-sys", "disk": map[string]any{"bus": "virtio"}},
		map[string]any{"name": "disk-extra", "disk": map[string]any{"bus": "virtio"}},
	}
	target := cleanupTargetVM("vm-target", volumes, disks)
	client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{vmResource: "VirtualMachineList"}, target)
	native := NewNativeWithClients(client, nil)
	v := cleanupVM("vm-target")
	v.Disks = []biz.Disk{{VolumeName: "disk-sys", Role: "system", Attached: true, Serial: "s1"}}
	_, ref, err := native.ApplyCloneTargetConfiguration(ctx, v, biz.CloneRequest{Name: "vm-clone-target", TargetName: "vm-target"})
	if !errors.Is(err, biz.ErrChanged) || !strings.Contains(err.Error(), "disk-extra") {
		t.Fatalf("unexpected failure: %v %v", ref, err)
	}
	for _, action := range client.Actions() {
		if action.GetVerb() == "patch" || action.GetVerb() == "update" {
			t.Fatalf("conflicting target was written: %v", action)
		}
	}
}

// TestDeleteNamedVolumesOwnership pins the failure-cleanup ownership rule: the
// recorded UID or a target owner reference is required, other PVCs stay.
func TestDeleteNamedVolumesOwnership(t *testing.T) {
	ctx := context.Background()
	ours := cleanupPVC("pvc-ours", "uid-1", nil)
	foreign := cleanupPVC("pvc-foreign", "uid-2", nil)
	owner := []any{map[string]any{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine", "name": "vm-target", "uid": "target-uid"}}
	owned := cleanupPVC("pvc-owned", "uid-3", owner)
	client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{pvcResource: "PersistentVolumeClaimList"}, ours, foreign, owned)
	native := NewNativeWithClients(client, nil)
	v := cleanupVM("vm-target")
	refs := []biz.VolumeRef{
		{VolumeName: "disk-sys", PVCName: "pvc-ours", PVCUID: "uid-1"},
		{VolumeName: "disk-data", PVCName: "pvc-foreign", PVCUID: "uid-9"},
		{VolumeName: "disk-extra", PVCName: "pvc-owned"},
	}
	done, err := native.DeleteNamedVolumes(ctx, v, refs)
	if err != nil || !done {
		t.Fatalf("cleanup did not converge: %v %v", done, err)
	}
	if _, err = client.Resource(pvcResource).Namespace("tenant-a").Get(ctx, "pvc-ours", metav1.GetOptions{}); !apierrors.IsNotFound(err) {
		t.Fatalf("recorded UID PVC survived: %v", err)
	}
	if _, err = client.Resource(pvcResource).Namespace("tenant-a").Get(ctx, "pvc-owned", metav1.GetOptions{}); !apierrors.IsNotFound(err) {
		t.Fatalf("target-owned PVC survived: %v", err)
	}
	if _, err = client.Resource(pvcResource).Namespace("tenant-a").Get(ctx, "pvc-foreign", metav1.GetOptions{}); err != nil {
		t.Fatalf("foreign PVC was removed: %v", err)
	}
}

// TestProjectClonePrefersTargetOutputs pins D9: the target's actual volume
// references win over the restore list for the cancellation hand-off.
func TestProjectClonePrefersTargetOutputs(t *testing.T) {
	ctx := context.Background()
	v := cleanupVM("vm-target")
	r := biz.CloneRequest{
		Name:         "vm-clone-target",
		OperationID:  "b492e0ba-f34e-4143-a9dc-94427c2d3bec",
		TargetName:   "vm-target",
		SMBiosSerial: "target-vm",
		Source:       biz.ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-a", Name: "vm-source", UID: "source-uid"},
	}
	volumes := []any{map[string]any{"name": "disk-sys", "persistentVolumeClaim": map[string]any{"claimName": "target-pvc"}}}
	target := cleanupTargetVM("vm-target", volumes, []any{map[string]any{"name": "disk-sys", "disk": map[string]any{"bus": "virtio"}}})
	clone := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "clone.kubevirt.io/v1beta1",
		"kind":       "VirtualMachineClone",
		"metadata": map[string]any{
			"name": r.Name, "namespace": "tenant-a", "uid": "clone-uid", "resourceVersion": "3",
			"labels":      toAnyMap(labelVM(v)),
			"annotations": map[string]any{CloneCreateOperationAnnotation: r.OperationID},
		},
		"spec": map[string]any{
			"source": map[string]any{"name": "vm-source"},
			"target": map[string]any{"name": "vm-target"},
		},
		"status": map[string]any{"phase": "Running", "restoreName": "restore-1"},
	}}
	restore := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "snapshot.kubevirt.io/v1beta1", "kind": "VirtualMachineRestore",
		"metadata": map[string]any{"name": "restore-1", "namespace": "tenant-a", "uid": "restore-uid"},
		"status": map[string]any{"restores": []any{
			map[string]any{"volumeName": "disk-sys", "persistentVolumeClaim": "restore-pvc"},
			map[string]any{"volumeName": "disk-data", "persistentVolumeClaim": "restore-data"},
		}},
	}}
	targetPVC := cleanupPVC("target-pvc", "target-pvc-uid", nil)
	restorePVC := cleanupPVC("restore-data", "restore-data-uid", nil)
	client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{
		vmResource:      "VirtualMachineList",
		cloneResource:   "VirtualMachineCloneList",
		restoreResource: "VirtualMachineRestoreList",
		pvcResource:     "PersistentVolumeClaimList",
	}, target, clone, restore, targetPVC, restorePVC)
	native := NewNativeWithClients(client, nil)
	state, err := native.ObserveClone(ctx, v, r)
	if err != nil {
		t.Fatal(err)
	}
	outputs := map[string]biz.VolumeRef{}
	for _, output := range state.Outputs {
		outputs[output.VolumeName] = output
	}
	if outputs["disk-sys"].PVCName != "target-pvc" || outputs["disk-sys"].PVCUID != "target-pvc-uid" {
		t.Fatalf("target reference was not preferred: %v", outputs)
	}
	if outputs["disk-data"].PVCName != "restore-data" || outputs["disk-data"].PVCUID != "restore-data-uid" {
		t.Fatalf("restore fallback was not kept: %v", outputs)
	}
}
