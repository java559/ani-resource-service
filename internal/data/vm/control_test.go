package vm

import (
	"context"
	restful "github.com/emicklei/go-restful/v3"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
	clientfeatures "k8s.io/client-go/features"
	featuretesting "k8s.io/client-go/features/testing"
	"k8s.io/client-go/rest"
)

func TestNativePowerUsesKubeVirtSubresource(t *testing.T) {
	featuretesting.SetFeatureDuringTest(t, clientfeatures.ClientsAllowCBOR, true)
	featuretesting.SetFeatureDuringTest(t, clientfeatures.ClientsPreferCBOR, true)
	v := biz.VM{TenantID: uuid.NewString(), ID: uuid.NewString(), Native: biz.ObjectRef{Name: "vm-control", Namespace: "tenant", UID: uuid.NewString()}, Spec: biz.Spec{Namespace: "tenant", NamespaceUID: uuid.NewString(), Digest: stringOfDigest()}}
	obj := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine", "metadata": map[string]any{"name": v.Native.Name, "namespace": v.Spec.Namespace, "uid": v.Native.UID}}}
	obj.SetResourceVersion("10")
	_ = unstructured.SetNestedMap(obj.Object, map[string]any{}, "spec", "template", "spec", "domain", "devices")
	obj.SetLabels(labelVM(v))
	namespace := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "Namespace", "metadata": map[string]any{"name": v.Spec.Namespace, "uid": v.Spec.NamespaceUID, "labels": map[string]any{"network.ani.io/tenant-id": v.TenantID, "network.ani.io/managed-by": "ani-network-service"}}}}
	client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), map[schema.GroupVersionResource]string{podResource: "PodList"}, obj, namespace)
	calls := 0
	// KubeVirt v1.9 api.go registers power routes with Consumes(MIME_ANY)
	// and no Produces declaration. A JSON-only Accept header yields 406.
	container := restful.NewContainer()
	ws := new(restful.WebService)
	ws.Path("/apis/subresources.kubevirt.io/v1/namespaces/tenant/virtualmachines")
	ws.Route(ws.PUT("/{name}/start").Consumes("*/*").To(func(req *restful.Request, response *restful.Response) {
		calls++
		if req.Request.Header.Get("Content-Type") != "application/json" {
			t.Errorf("raw JSON body missing Content-Type")
		}
		response.WriteHeader(http.StatusAccepted)
	}))
	container.Add(ws)
	s := httptest.NewServer(container)
	defer s.Close()
	native, e := NewNative(client, &rest.Config{Host: s.URL})
	if e != nil {
		t.Fatal(e)
	}
	if e = native.Start(context.Background(), v); e != nil {
		t.Fatalf("native start failed before/after transport: %v (calls=%d)", e, calls)
	}
	if calls != 1 {
		t.Fatalf("native request not issued exactly once: %d", calls)
	}
	if _, e = client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(context.Background(), v.Native.Name, metav1.GetOptions{}); e != nil {
		t.Fatal(e)
	}
}
func stringOfDigest() string {
	return "5b44b39b65fb1d05ec48cd692692fa19044b19d48657d1d78ce82bbd102f1f0f"
}
