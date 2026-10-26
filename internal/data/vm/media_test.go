package vm

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic/fake"
	ktesting "k8s.io/client-go/testing"
)

func mediaNativeFixture(t *testing.T) (*Native, biz.VM, *fake.FakeDynamicClient) {
	t.Helper()
	id := uuid.NewString()
	tenant := uuid.NewString()
	namespaceUID := uuid.NewString()
	source := biz.Source{Key: "linux-install", Kind: "INSTALL_ISO", ClusterID: uuid.NewString(), Namespace: "media", PVCName: "linux-iso", PVCUID: uuid.NewString(), Checksum: strings.Repeat("a", 64), VolumeMode: "Filesystem", ContentType: "kubevirt", RequestedBytes: 2 << 30, VirtualBytes: 1 << 30, OSFamily: "linux", OSVersion: "24.04", Architecture: "amd64", Firmware: "uefi", RootBus: "virtio", DataBus: "scsi", NICModel: "virtio"}
	v := biz.VM{TenantID: tenant, ID: id, Native: biz.ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-test", Name: "vm-" + id, UID: uuid.NewString()}, Spec: biz.Spec{Digest: strings.Repeat("b", 64), Namespace: "tenant-test", NamespaceUID: namespaceUID, ClusterID: source.ClusterID, StorageClass: "block-sc", MediaStorageClass: "iso-sc", Media: source, BootDevice: "INSTALL_MEDIA"}}
	v.Disks = []biz.Disk{biz.NewInitialDisk(id, "system", "system", "system", 12)}
	if e := biz.InitializeMedia(&v); e != nil {
		t.Fatal(e)
	}
	namespace := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "Namespace", "metadata": map[string]any{"name": v.Spec.Namespace, "uid": namespaceUID, "labels": map[string]any{"network.ani.io/tenant-id": tenant, "network.ani.io/managed-by": "ani-network-service"}}}}
	shared := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": map[string]any{"name": source.PVCName, "namespace": source.Namespace, "uid": source.PVCUID, "annotations": map[string]any{"vm.ani.io/source-sha256": source.Checksum}}, "spec": map[string]any{"volumeMode": "Filesystem"}, "status": map[string]any{"phase": "Bound"}}}
	root := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine", "metadata": map[string]any{"name": v.Native.Name, "namespace": v.Spec.Namespace, "uid": v.Native.UID}, "spec": map[string]any{"template": map[string]any{"spec": map[string]any{"volumes": []any{map[string]any{"name": v.Disks[0].VolumeName, "dataVolume": map[string]any{"name": v.Disks[0].DV.Name}}, map[string]any{"name": "guest-initialization", "cloudInitNoCloud": map[string]any{"secretRef": map[string]any{"name": "private-init"}}}}, "domain": map[string]any{"devices": map[string]any{"disks": []any{map[string]any{"name": v.Disks[0].VolumeName, "disk": map[string]any{"bus": "virtio"}, "bootOrder": int64(1)}, map[string]any{"name": "guest-initialization", "disk": map[string]any{"bus": "virtio"}}}}}}}}}}
	root.SetLabels(labelVM(v))
	client := fake.NewSimpleDynamicClient(runtime.NewScheme(), namespace, shared, root)
	client.PrependReactor("create", "*", func(action ktesting.Action) (bool, runtime.Object, error) {
		object := action.(ktesting.CreateAction).GetObject().(*unstructured.Unstructured)
		object.SetUID(types.UID(uuid.NewString()))
		return false, nil, nil
	})
	return NewNativeWithClients(client, nil), v, client
}

func TestMediaNativeFilesystemCopyHostInitializationAndGuestReadonly(t *testing.T) {
	native, v, client := mediaNativeFixture(t)
	root, err := client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(context.Background(), v.Native.Name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	volumes, disks, err := mediaLists(root, "spec", "template", "spec")
	if err != nil {
		t.Fatal(err)
	}
	volumes, disks, err = native.MediaTemplate(v, nil, volumes, disks)
	if err != nil {
		t.Fatal(err)
	}
	claim := volumes[len(volumes)-1].(map[string]any)["persistentVolumeClaim"].(map[string]any)
	optical := disks[len(disks)-1].(map[string]any)["cdrom"].(map[string]any)
	if claim["readOnly"] == true {
		t.Fatal("VM-owned Filesystem ISO copy prevents KubeVirt directory ownership initialization: read-only file system")
	}
	if _, present := claim["readOnly"]; present {
		t.Fatal("Filesystem host default must use the canonical omitted readOnly field")
	}
	if claim["claimName"] != v.MediaCopies[0].PVC.Name || claim["claimName"] == v.Spec.Media.PVCName || optical["readonly"] != true || optical["bus"] != "sata" {
		t.Fatal("host initialization changed the dedicated source or Guest CD-ROM readonly boundary")
	}
	t.Logf("native optical render: host_readOnly=%v dedicated_claim=%v guest_readonly=%v guest_bus=%v", claim["readOnly"], claim["claimName"], optical["readonly"], optical["bus"])
	for _, hostMode := range []string{"writable", "default", "legacy-readonly"} {
		t.Run(hostMode, func(t *testing.T) {
			actual := plainSlice(volumes)
			pvc := actual[len(actual)-1].(map[string]any)["persistentVolumeClaim"].(map[string]any)
			switch hostMode {
			case "writable":
				pvc["readOnly"] = false
			case "default":
				delete(pvc, "readOnly")
			case "legacy-readonly":
				pvc["readOnly"] = true
			}
			id, err := runtimeMediaCopy(v, "INSTALL", actual, disks)
			if err != nil || id != v.MediaCopies[0].ID {
				t.Fatal("template/runtime chain lost the frozen copy or legacy cleanup compatibility", id, err)
			}
		})
	}
	badDisks := plainSlice(disks)
	badDisks[len(badDisks)-1].(map[string]any)["cdrom"].(map[string]any)["readonly"] = false
	if _, err = runtimeMediaCopy(v, "INSTALL", volumes, badDisks); !errors.Is(err, biz.ErrOwnership) {
		t.Fatal("Guest-writable optical device accepted", err)
	}
	badVolumes := plainSlice(volumes)
	badVolumes[len(badVolumes)-1].(map[string]any)["persistentVolumeClaim"].(map[string]any)["claimName"] = v.Spec.Media.PVCName
	if _, err = runtimeMediaCopy(v, "INSTALL", badVolumes, disks); !errors.Is(err, biz.ErrOwnership) {
		t.Fatal("shared source mounted instead of the frozen VM-owned copy", err)
	}
	block := v
	block.MediaCopies = append([]biz.MediaCopy(nil), v.MediaCopies...)
	block.MediaCopies[0].Source.VolumeMode = "Block"
	blockVolumes, blockDisks, err := native.MediaTemplate(block, nil, volumes, disks)
	if err != nil {
		t.Fatal(err)
	}
	blockClaim := blockVolumes[len(blockVolumes)-1].(map[string]any)["persistentVolumeClaim"].(map[string]any)
	if blockClaim["readOnly"] != true {
		t.Fatal("Block media host readonly policy changed")
	}
	if _, err = runtimeMediaCopy(block, "INSTALL", blockVolumes, blockDisks); err != nil {
		t.Fatal(err)
	}
	blockClaim["readOnly"] = false
	if _, err = runtimeMediaCopy(block, "INSTALL", blockVolumes, blockDisks); !errors.Is(err, biz.ErrOwnership) {
		t.Fatal("Block media writable host mount accepted", err)
	}
}

func TestMediaTemplateAcceptsTypedPVCReadonlyDefaultWithoutUpdate(t *testing.T) {
	for _, explicitFalse := range []bool{false, true} {
		t.Run(map[bool]string{false: "typed-omitempty", true: "explicit-false"}[explicitFalse], func(t *testing.T) {
			ctx := context.Background()
			native, v, client := mediaNativeFixture(t)
			copy, err := native.EnsureMediaCopy(ctx, v, v.MediaCopies[0])
			if err != nil {
				t.Fatal(err)
			}
			v.MediaCopies[0] = copy
			root, err := client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, v.Native.Name, metav1.GetOptions{})
			if err != nil {
				t.Fatal(err)
			}
			volumes, disks, err := mediaLists(root, "spec", "template", "spec")
			if err != nil {
				t.Fatal(err)
			}
			volumes, disks, err = native.MediaTemplate(v, nil, volumes, disks)
			if err != nil {
				t.Fatal(err)
			}
			pvc := volumes[len(volumes)-1].(map[string]any)["persistentVolumeClaim"].(map[string]any)
			pvc["readOnly"] = false
			encoded, err := json.Marshal(pvc)
			if err != nil {
				t.Fatal(err)
			}
			var typed corev1.PersistentVolumeClaimVolumeSource
			if err = json.Unmarshal(encoded, &typed); err != nil {
				t.Fatal(err)
			}
			encoded, err = json.Marshal(typed)
			if err != nil {
				t.Fatal(err)
			}
			var actual map[string]any
			if err = json.Unmarshal(encoded, &actual); err != nil {
				t.Fatal(err)
			}
			if _, present := actual["readOnly"]; present {
				t.Fatal("pinned Kubernetes PVC source did not omit false readOnly")
			}
			if explicitFalse {
				actual["readOnly"] = false
			}
			volumes[len(volumes)-1].(map[string]any)["persistentVolumeClaim"] = actual
			if err = unstructured.SetNestedSlice(root.Object, volumes, "spec", "template", "spec", "volumes"); err != nil {
				t.Fatal(err)
			}
			if err = unstructured.SetNestedSlice(root.Object, disks, "spec", "template", "spec", "domain", "devices", "disks"); err != nil {
				t.Fatal(err)
			}
			if _, err = client.Resource(vmResource).Namespace(v.Spec.Namespace).Update(ctx, root, metav1.UpdateOptions{}); err != nil {
				t.Fatal(err)
			}
			observed, err := native.ObserveMedia(ctx, v)
			if err != nil || !observed.TemplateMatches {
				t.Fatal("typed PVC readonly default made the accepted media template diverge", observed.TemplateMatches, err)
			}
			client.ClearActions()
			if _, err = native.ApplyMedia(ctx, v); err != nil {
				t.Fatal(err)
			}
			for _, action := range client.Actions() {
				if action.GetVerb() == "update" && action.GetResource() == vmResource {
					t.Fatal("equivalent PVC readonly default caused a redundant VM update")
				}
			}
		})
	}
}

func TestMediaNativeDedicatedReadonlyCopySourceRecheckAndUIDFence(t *testing.T) {
	ctx := context.Background()
	native, v, client := mediaNativeFixture(t)
	planned := v.MediaCopies[0]
	copy, e := native.EnsureMediaCopy(ctx, v, planned)
	if e != nil || copy.DV.UID == "" {
		t.Fatal(copy, e)
	}
	v.MediaCopies[0] = copy
	dv, e := client.Resource(dvResource).Namespace(v.Spec.Namespace).Get(ctx, copy.DV.Name, metav1.GetOptions{})
	if e != nil {
		t.Fatal(e)
	}
	mode, _, _ := unstructured.NestedString(dv.Object, "spec", "storage", "volumeMode")
	if mode != "Filesystem" || !owned(dv, "VirtualMachine", v.Native.UID) {
		t.Fatal("ISO target mode/owner was not frozen", dv.Object)
	}
	shared, e := client.Resource(pvcResource).Namespace(v.Spec.Media.Namespace).Get(ctx, v.Spec.Media.PVCName, metav1.GetOptions{})
	if e != nil || len(shared.GetOwnerReferences()) != 0 {
		t.Fatal("shared source was adopted", e)
	}
	root, _ := client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, v.Native.Name, metav1.GetOptions{})
	volumes, disks, _ := mediaLists(root, "spec", "template", "spec")
	newVolumes, newDisks, e := native.MediaTemplate(v, nil, volumes, disks)
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(newVolumes[1], volumes[1]) || !reflect.DeepEqual(newDisks[1], disks[1]) {
		t.Fatal("cloud-init device was changed or duplicated")
	}
	optical := newDisks[len(newDisks)-1].(map[string]any)["cdrom"].(map[string]any)
	claim := newVolumes[len(newVolumes)-1].(map[string]any)["persistentVolumeClaim"].(map[string]any)
	if optical["bus"] != "sata" || optical["readonly"] != true || claim["readOnly"] == true || claim["claimName"] != copy.PVC.Name || claim["claimName"] == v.Spec.Media.PVCName {
		t.Fatal("optical device changed the Guest readonly or private host-initialization boundary")
	}
	if e = unstructured.SetNestedField(dv.Object, "Succeeded", "status", "phase"); e != nil {
		t.Fatal(e)
	}
	if _, e = client.Resource(dvResource).Namespace(v.Spec.Namespace).Update(ctx, dv, metav1.UpdateOptions{}); e != nil {
		t.Fatal(e)
	}
	if e = client.Resource(pvcResource).Namespace(v.Spec.Media.Namespace).Delete(ctx, v.Spec.Media.PVCName, metav1.DeleteOptions{}); e != nil {
		t.Fatal(e)
	}
	if _, e = native.EnsureMediaCopy(ctx, v, copy); e != nil {
		t.Fatal("registered copy incorrectly depends on removed source", e)
	}
	dv.SetUID(types.UID(uuid.NewString()))
	if _, e = client.Resource(dvResource).Namespace(v.Spec.Namespace).Update(ctx, dv, metav1.UpdateOptions{}); e != nil {
		t.Fatal(e)
	}
	if _, e = native.EnsureMediaCopy(ctx, v, copy); !errors.Is(e, biz.ErrOwnership) {
		t.Fatal("same-name DV replacement adopted", e)
	}
	// A new unmaterialized copy must recheck its frozen original source UID.
	native, fresh, client := mediaNativeFixture(t)
	shared, _ = client.Resource(pvcResource).Namespace(fresh.Spec.Media.Namespace).Get(ctx, fresh.Spec.Media.PVCName, metav1.GetOptions{})
	shared.SetUID(types.UID(uuid.NewString()))
	_, _ = client.Resource(pvcResource).Namespace(shared.GetNamespace()).Update(ctx, shared, metav1.UpdateOptions{})
	if _, e = native.EnsureMediaCopy(ctx, fresh, fresh.MediaCopies[0]); !errors.Is(e, biz.ErrChanged) {
		t.Fatal("source replacement before clone accepted", e)
	}
}
func TestMediaPersistentOnlyUpdateReportsActualOldVMIAndAllowsNextStart(t *testing.T) {
	ctx := context.Background()
	native, v, client := mediaNativeFixture(t)
	old, e := native.EnsureMediaCopy(ctx, v, v.MediaCopies[0])
	if e != nil {
		t.Fatal(e)
	}
	v.MediaCopies[0] = old
	root, _ := client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, v.Native.Name, metav1.GetOptions{})
	volumes, disks, _ := mediaLists(root, "spec", "template", "spec")
	volumes, disks, e = native.MediaTemplate(v, nil, volumes, disks)
	if e != nil {
		t.Fatal(e)
	}
	_ = unstructured.SetNestedSlice(root.Object, volumes, "spec", "template", "spec", "volumes")
	_ = unstructured.SetNestedSlice(root.Object, disks, "spec", "template", "spec", "domain", "devices", "disks")
	_, _ = client.Resource(vmResource).Namespace(v.Spec.Namespace).Update(ctx, root, metav1.UpdateOptions{})
	template, _, _ := unstructured.NestedMap(root.Object, "spec", "template", "spec")
	vmi := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachineInstance", "metadata": map[string]any{"name": v.Native.Name, "namespace": v.Spec.Namespace, "uid": uuid.NewString()}, "spec": template}}
	vmi.SetOwnerReferences(ownReference(v))
	_, _ = client.Resource(vmiResource).Namespace(v.Spec.Namespace).Create(ctx, vmi, metav1.CreateOptions{})
	v.MediaSlots[0].DesiredCopyID = ""
	v.CurrentBootDevice = "SYSTEM_DISK"
	observed, e := native.ApplyMedia(ctx, v)
	if e != nil {
		t.Fatal(e)
	}
	if !observed.TemplateMatches || !observed.HasVMI || !observed.Slots[0].PendingRestart || observed.Slots[0].EffectiveNow || observed.Slots[0].ObservedCopyID != old.ID {
		t.Fatal("template-only ejection falsely claimed runtime removal", observed)
	}
	if _, e = client.Resource(dvResource).Namespace(v.Spec.Namespace).Get(ctx, old.DV.Name, metav1.GetOptions{}); e != nil {
		t.Fatal("ejection prematurely deleted retained copy", e)
	}
	for _, action := range client.Actions() {
		if action.GetVerb() == "update" && action.GetResource() == vmiResource {
			t.Fatal("media change silently rewrote running VMI")
		}
	}
	root, _ = client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, v.Native.Name, metav1.GetOptions{})
	template, _, _ = unstructured.NestedMap(root.Object, "spec", "template", "spec")
	vmi.Object["spec"] = template
	_, _ = client.Resource(vmiResource).Namespace(v.Spec.Namespace).Update(ctx, vmi, metav1.UpdateOptions{})
	observed, e = native.ObserveMedia(ctx, v)
	if e != nil || observed.Slots[0].PendingRestart || observed.Slots[0].ObservedCopyID != "" {
		t.Fatal("new VMI media was not refreshed", observed, e)
	}
}
func TestMediaRestoredPVCOnlyAndClosingAbsenceNeverReclone(t *testing.T) {
	ctx := context.Background()
	native, v, client := mediaNativeFixture(t)
	copy := v.MediaCopies[0]
	copy.DV = biz.ObjectRef{}
	copy.PVC = biz.ObjectRef{Kind: "PersistentVolumeClaim", Namespace: v.Spec.Namespace, Name: "restore-media", UID: uuid.NewString()}
	pvc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": map[string]any{"name": copy.PVC.Name, "namespace": v.Spec.Namespace, "uid": copy.PVC.UID}, "spec": map[string]any{"volumeMode": "Filesystem"}, "status": map[string]any{"phase": "Bound"}}}
	pvc.SetOwnerReferences(ownReference(v))
	_, _ = client.Resource(pvcResource).Namespace(v.Spec.Namespace).Create(ctx, pvc, metav1.CreateOptions{})
	// Fake create assigns a server UID; register that exact trusted restore UID.
	pvc, _ = client.Resource(pvcResource).Namespace(v.Spec.Namespace).Get(ctx, pvc.GetName(), metav1.GetOptions{})
	copy.PVC.UID = string(pvc.GetUID())
	if _, e := native.EnsureMediaCopy(ctx, v, copy); e != nil {
		t.Fatal("trusted restored PVC not observed", e)
	}
	for _, action := range client.Actions() {
		if action.GetVerb() == "create" && action.GetResource() == dvResource {
			t.Fatal("restore reconstructed original source clone")
		}
	}
	v.Closing = true
	_ = client.Resource(vmResource).Namespace(v.Spec.Namespace).Delete(ctx, v.Native.Name, metav1.DeleteOptions{})
	_ = client.Resource(pvcResource).Namespace(v.Spec.Namespace).Delete(ctx, copy.PVC.Name, metav1.DeleteOptions{})
	if _, done, e := native.DeleteMediaCopy(ctx, v, copy); e != nil || !done {
		t.Fatal("known absent media after root deletion did not finish", done, e)
	}
	if _, done, e := native.DeleteMediaCopy(ctx, v, v.MediaCopies[0]); e != nil || !done {
		t.Fatal("never-created media did not finish", done, e)
	}
}
