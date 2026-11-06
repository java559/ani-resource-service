package vm

import (
	"context"
	"strings"
	"testing"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
)

// The upload server is a CDI-owned Pod. When an upload fails it exits Failed and is
// left behind; the DataVolume then sits in foregroundDeletion waiting for a dependent
// that never goes away, and pvc-protection keeps the prime PVC Terminating forever.
// Cleanup must retire that failed Pod itself: no operator is in the loop at 3am.

const (
	uploadPodLabel = "cdi-upload-server"
	cleanupTenant  = "64c5c35a-f554-432c-b4bc-7b4f36f08420"
	cleanupImageID = "c1d0f0a1-1111-4222-8333-444455556666"
	cleanupPVCUID  = "pvc-uid-cleanup"
)

func cleanupRecord() biz.Image {
	i := biz.Image{TenantID: cleanupTenant, ID: cleanupImageID, Kind: "DISK_IMAGE", State: biz.StateDeleting,
		CleanupState: biz.CleanupRunning}
	i.Native = biz.ImageNative{Namespace: "tenant-" + cleanupTenant, ClusterID: "cluster-a",
		DVName: imageName(i), DVUID: "dv-uid-cleanup",
		PVCName: imageName(i), PVCUID: cleanupPVCUID, SourceVolumeMode: "Block"}
	// The probe found the populator PVCs; they are what cleanup is draining now.
	i.Cleanup = []biz.ObjectRef{
		{Kind: "PersistentVolumeClaim", Namespace: i.Native.Namespace, Name: "prime-" + cleanupPVCUID, UID: "prime-uid"},
		{Kind: "PersistentVolumeClaim", Namespace: i.Native.Namespace, Name: "prime-" + cleanupPVCUID + "-scratch", UID: "scratch-uid"},
	}
	return i
}

func uploadPod(namespace, primePVC string, phase string, uid string) *unstructured.Unstructured {
	pod := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1", "kind": "Pod",
		"metadata": map[string]any{
			"name": "cdi-upload-" + primePVC, "namespace": namespace, "uid": uid,
			"labels": map[string]any{
				"cdi.kubevirt.io":              uploadPodLabel,
				"service":                      "cdi-upload-" + primePVC,
				"app.kubernetes.io/managed-by": "cdi-controller",
			},
		},
		"status": map[string]any{"phase": phase},
	}}
	pod.SetOwnerReferences([]metav1.OwnerReference{{
		APIVersion: "v1", Kind: "PersistentVolumeClaim", Name: primePVC, UID: "prime-uid", Controller: boolPtr(true),
	}})
	return pod
}

func cleanupCluster(t *testing.T, i biz.Image, extra ...runtime.Object) *fake.FakeDynamicClient {
	t.Helper()
	dv := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "cdi.kubevirt.io/v1beta1", "kind": "DataVolume",
		"metadata": map[string]any{"name": i.Native.DVName, "namespace": i.Native.Namespace, "uid": i.Native.DVUID,
			"deletionTimestamp": "2026-11-05T06:21:25Z", "finalizers": []any{"foregroundDeletion"},
			"labels": map[string]any{"vm.ani.io/image-id": i.ID, "ani.io/tenant-id": i.TenantID, "vm.ani.io/managed-by": biz.Owner}},
	}}
	prime := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1", "kind": "PersistentVolumeClaim",
		"metadata": map[string]any{"name": "prime-" + cleanupPVCUID, "namespace": i.Native.Namespace, "uid": "prime-uid",
			"deletionTimestamp": "2026-11-05T06:21:25Z", "finalizers": []any{"kubernetes.io/pvc-protection", "foregroundDeletion"}},
	}}
	objects := append([]runtime.Object{dv, prime}, extra...)
	return fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{
		dvResource: "DataVolumeList", pvcResource: "PersistentVolumeClaimList",
		pvResource: "PersistentVolumeList", podResource: "PodList"}, objects...)
}

func patchedPods(c *fake.FakeDynamicClient) []string {
	var names []string
	for _, action := range c.Actions() {
		if action.GetVerb() != "patch" || action.GetResource().Resource != "pods" {
			continue
		}
		if patched, ok := action.(interface{ GetName() string }); ok {
			names = append(names, patched.GetName())
		}
	}
	return names
}

// TestDeleteImageObjectsRetiresTheFailedUploader is the DEV-B40 guard: a Failed upload
// Pod keeps its prime PVC alive through pvc-protection, so foregroundDeletion never
// finishes and the record stays DELETING forever. Cleanup has to remove that Pod.
func TestDeleteImageObjectsRetiresTheFailedUploader(t *testing.T) {
	i := cleanupRecord()
	pod := uploadPod(i.Native.Namespace, "prime-"+cleanupPVCUID, "Failed", "pod-uid-failed")
	pod.SetFinalizers([]string{"foregroundDeletion"})
	cluster := cleanupCluster(t, i, pod)
	n := &ImageNative{client: cluster}

	out, done, err := n.DeleteImageObjects(context.Background(), i)
	if err != nil {
		t.Fatalf("cleanup refused: %v", err)
	}
	if done {
		t.Fatal("cleanup reported completion while the prime PVC is still present")
	}
	removed := patchedPods(cluster)
	if len(removed) != 1 || !strings.HasPrefix(removed[0], "cdi-upload-prime-") {
		t.Fatalf("the failed uploader kept the finalizer deadlock: patched=%v", removed)
	}
	if out.Native.DVName == "" {
		t.Fatal("cleanup dropped the DataVolume registration while still draining")
	}
}

// TestDeleteImageObjectsLeavesARunningUploader keeps the fence: an upload in progress is
// somebody's live transfer, and killing it would corrupt data instead of draining.
func TestDeleteImageObjectsLeavesARunningUploader(t *testing.T) {
	i := cleanupRecord()
	pod := uploadPod(i.Native.Namespace, "prime-"+cleanupPVCUID, "Running", "pod-uid-running")
	pod.SetFinalizers([]string{"foregroundDeletion"})
	cluster := cleanupCluster(t, i, pod)
	n := &ImageNative{client: cluster}

	if _, _, err := n.DeleteImageObjects(context.Background(), i); err != nil {
		t.Fatalf("cleanup refused: %v", err)
	}
	if removed := patchedPods(cluster); len(removed) != 0 {
		t.Fatalf("a live upload was killed: %v", removed)
	}
}

// TestDeleteImageObjectsLeavesAForeignUploaderAlone: uploader Pods share one label across
// the whole namespace, so only the owner reference proves the identity. A Pod owned by
// another PVC must be skipped, never removed.
func TestDeleteImageObjectsLeavesAForeignUploaderAlone(t *testing.T) {
	i := cleanupRecord()
	pod := uploadPod(i.Native.Namespace, "prime-"+cleanupPVCUID, "Failed", "pod-uid-foreign")
	pod.SetFinalizers([]string{"foregroundDeletion"})
	pod.SetOwnerReferences([]metav1.OwnerReference{{
		APIVersion: "v1", Kind: "PersistentVolumeClaim", Name: "prime-someone-else", UID: "other-uid",
	}})
	cluster := cleanupCluster(t, i, pod)
	n := &ImageNative{client: cluster}

	if _, _, err := n.DeleteImageObjects(context.Background(), i); err != nil {
		t.Fatalf("cleanup refused: %v", err)
	}
	if removed := patchedPods(cluster); len(removed) != 0 {
		t.Fatalf("a foreign uploader was deleted: %v", removed)
	}
}

func boolPtr(b bool) *bool { return &b }
