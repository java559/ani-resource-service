package vm

import (
	"context"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic/fake"
	ktesting "k8s.io/client-go/testing"
)

func TestNativeColdDiskReferencesWFFCAndStableIdentity(t *testing.T) {
	ctx := context.Background()
	v := biz.VM{TenantID: uuid.NewString(), ID: uuid.NewString(), Spec: biz.Spec{Namespace: "disk-test", Digest: stringDigestUnit('a'), StorageClass: "block"}}
	v.Native = biz.ObjectRef{Kind: "VirtualMachine", Name: "vm-" + v.ID, Namespace: v.Spec.Namespace, UID: uuid.NewString()}
	vm := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine", "metadata": map[string]any{"name": v.Native.Name, "namespace": v.Spec.Namespace, "uid": v.Native.UID}, "spec": map[string]any{"runStrategy": "Manual", "template": map[string]any{"spec": map[string]any{"volumes": []any{}, "domain": map[string]any{"devices": map[string]any{"disks": []any{}}}}}}}}
	vm.SetLabels(labelVM(v))
	c := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{podResource: "PodList"}, vm)
	c.PrependReactor("create", "datavolumes", func(a ktesting.Action) (bool, runtime.Object, error) {
		a.(ktesting.CreateAction).GetObject().(*unstructured.Unstructured).SetUID(types.UID(uuid.NewString()))
		return false, nil, nil
	})
	n := NewNativeWithClients(c, nil)
	d := biz.NewInitialDisk(v.ID, "later", "late-data", "data", 2)
	d.Attached = false
	d.DV.Namespace = v.Spec.Namespace
	var e error
	d, e = n.EnsureVolume(ctx, v, d)
	if e != nil {
		t.Fatal(e)
	}
	v.Disks = []biz.Disk{d}
	if d.PVC.UID != "" || d.DV.UID == "" {
		t.Fatal("fixture must remain without a consumer/PVC")
	}
	for _, attached := range []bool{true, false, true, false} {
		for attempt := 0; attempt < 2; attempt++ {
			done, e := n.EnsureDiskAttachment(ctx, v, d, attached)
			if e != nil {
				t.Fatal(e)
			}
			if done != (attempt == 1) {
				t.Fatalf("persistent-only convergence %t at attempt%d", done, attempt)
			}
		}
		v.Disks[0].Attached = attached
		clear, e := n.DiskUnreferenced(ctx, v, d)
		if e != nil || clear == attached {
			t.Fatalf("reference proof %t attached%t: %v", clear, attached, e)
		}
	}
	objects, e := c.Resource(dvResource).Namespace(v.Spec.Namespace).Get(ctx, d.DV.Name, metav1.GetOptions{})
	if e != nil || string(objects.GetUID()) != d.DV.UID {
		t.Fatal("cold cycle replaced independent volume", e)
	}
	current, _ := c.Resource(vmResource).Namespace(v.Spec.Namespace).Get(ctx, v.Native.Name, metav1.GetOptions{})
	current.SetUID(types.UID(uuid.NewString()))
	_, _ = c.Resource(vmResource).Namespace(v.Spec.Namespace).Update(ctx, current, metav1.UpdateOptions{})
	if _, e = n.EnsureDiskAttachment(ctx, v, d, true); e != biz.ErrOwnership {
		t.Fatal("same-name VM replacement adopted", e)
	}
}

func stringDigestUnit(c byte) string {
	b := make([]byte, 64)
	for i := range b {
		b[i] = c
	}
	return string(b)
}

func TestNativeDeclarativeHotplugUsesPinnedVersionDefaults(t *testing.T) {
	for _, tc := range []struct {
		name, version     string
		enabled, disabled []any
		want              bool
	}{
		{"beta-default", "v1.9.0", nil, nil, true},
		{"beta-disabled", "v1.9.0", nil, []any{"DeclarativeHotplugVolumes"}, false},
		{"enable-precedence", "v1.9.0", []any{"DeclarativeHotplugVolumes"}, []any{"DeclarativeHotplugVolumes"}, true},
		{"legacy-precedence", "v1.9.0", []any{"DeclarativeHotplugVolumes", "HotplugVolumes"}, nil, false},
		{"unknown-version", "v1.8.0", nil, nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dev := map[string]any{}
			if tc.enabled != nil {
				dev["featureGates"] = tc.enabled
			}
			if tc.disabled != nil {
				dev["disabledFeatureGates"] = tc.disabled
			}
			kv := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "kubevirt.io/v1", "kind": "KubeVirt", "metadata": map[string]any{"name": "kubevirt", "namespace": "kubevirt"}, "status": map[string]any{"observedKubeVirtVersion": tc.version}, "spec": map[string]any{"configuration": map[string]any{"developerConfiguration": dev}}}}
			c := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{kubevirtResource: "KubeVirtList"}, kv)
			enabled, e := NewNativeWithClients(c, nil).declarativeHotplugEnabled(context.Background())
			if e != nil || enabled != tc.want {
				t.Fatalf("enabled%t expected%t: %v", enabled, tc.want, e)
			}
		})
	}
}
