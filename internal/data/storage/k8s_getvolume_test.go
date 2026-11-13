package storage

import (
	"context"
	"testing"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/storage"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/dynamic/fake"
	ktesting "k8s.io/client-go/testing"
)

// ST11: the shared GetVolume issues exactly one PVC GET — no StorageClass, PV
// or list traffic — and keeps the normal PVC projection intact.
func TestGetVolumeIssuesOnlyPVCGGet(t *testing.T) {
	pvc := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1",
		"kind":       "PersistentVolumeClaim",
		"metadata": map[string]any{
			"name": "dataset", "namespace": "tenant-a", "uid": "pvc-uid-1", "resourceVersion": "7",
			"labels": map[string]any{biz.LabelManagedBy: biz.ManagedByValue, biz.LabelTenantID: "tenant-a"},
		},
		"spec": map[string]any{
			"storageClassName": "fast", "volumeName": "pv-1",
			"resources": map[string]any{"requests": map[string]any{"storage": "10Gi"}},
		},
		"status": map[string]any{"phase": "Bound", "capacity": map[string]any{"storage": "10Gi"}},
	}}
	client := fake.NewSimpleDynamicClient(runtime.NewScheme(), pvc)
	gets := map[string]int{}
	client.PrependReactor("*", "*", func(action ktesting.Action) (bool, runtime.Object, error) {
		if action.GetVerb() == "get" {
			gets[action.GetResource().Resource]++
		}
		return false, nil, nil
	})
	adapter, err := NewKubernetes(client)
	if err != nil {
		t.Fatal(err)
	}
	record, err := adapter.GetVolume(context.Background(), "tenant-a", "dataset")
	if err != nil {
		t.Fatal(err)
	}
	if record.Name != "dataset" || record.UID != "pvc-uid-1" || record.Phase != "Bound" ||
		record.StorageClassName != "fast" || record.PVName != "pv-1" || record.RequestedBytes != 10<<30 {
		t.Fatal("PVC projection changed", record)
	}
	if gets["persistentvolumeclaims"] != 1 || gets["storageclasses"] != 0 || gets["persistentvolumes"] != 0 {
		t.Fatal("GetVolume must issue only the PVC GET", gets)
	}
}
