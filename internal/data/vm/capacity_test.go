package vm

import (
	"context"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic/fake"
)

func TestNativeBlockCapacityIsObservedNotAssumed(t *testing.T) {
	v := biz.VM{TenantID: uuid.NewString(), ID: uuid.NewString(), Native: biz.ObjectRef{UID: uuid.NewString()}, Spec: biz.Spec{Namespace: "tenant", Digest: "digest"}}
	d := biz.NewInitialDisk(v.ID, "system", "system", "system", 8)
	d.DV.Namespace = v.Spec.Namespace
	d.DV.UID = uuid.NewString()
	dv := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "cdi.kubevirt.io/v1beta1", "kind": "DataVolume", "metadata": map[string]any{"name": d.DV.Name, "namespace": v.Spec.Namespace, "uid": d.DV.UID}}}
	dv.SetLabels(labelVM(v))
	dv.SetOwnerReferences(ownReference(v))
	controller := true
	pvc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": map[string]any{"name": d.DV.Name, "namespace": v.Spec.Namespace, "uid": uuid.NewString()}, "spec": map[string]any{"volumeMode": "Block"}, "status": map[string]any{"phase": "Bound", "capacity": map[string]any{"storage": "8Gi"}}}}
	pvc.SetOwnerReferences([]metav1.OwnerReference{{APIVersion: "cdi.kubevirt.io/v1beta1", Kind: "DataVolume", Name: d.DV.Name, UID: types.UID(d.DV.UID), Controller: &controller}})
	c := fake.NewSimpleDynamicClient(runtime.NewScheme(), dv, pvc)
	n := NewNativeWithClients(c, nil)
	observed, e := n.observeDisk(context.Background(), v, d)
	if e != nil || observed.RequestedBytes != 8000000000 || observed.ObservedCapacityBytes != 8589934592 {
		t.Fatalf("requested GB and native actual capacity conflated: %+v %v", observed, e)
	}
	_ = unstructured.SetNestedField(pvc.Object, "7G", "status", "capacity", "storage")
	if _, e = c.Resource(pvcResource).Namespace(v.Spec.Namespace).Update(context.Background(), pvc, metav1.UpdateOptions{}); e != nil {
		t.Fatal(e)
	}
	if _, e = n.observeDisk(context.Background(), v, d); e != biz.ErrChanged {
		t.Fatalf("undersized bound Block volume accepted: %v", e)
	}
}
