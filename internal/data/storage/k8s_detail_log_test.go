package storage

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"strings"
	"testing"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/storage"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
	ktesting "k8s.io/client-go/testing"
)

// ST21: with the real Kubernetes adapter behind the use case, a Forbidden PVC
// read keeps its native cause on the wrap chain (C08 vehicle), and the use-case
// log record extracts error_source/error_code through logfields. The extraction
// table itself is covered by the logfields package tests.
func TestDetailLogExtractsKubernetesForbiddenCause(t *testing.T) {
	tenant := "11111111-1111-4111-8111-111111111111"
	namespace := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1",
		"kind":       "Namespace",
		"metadata": map[string]any{
			"name": "tenant-" + tenant, "uid": "ns-uid",
			"labels": map[string]any{biz.NamespaceManagedBy: biz.NamespaceOwner, biz.NamespaceTenantID: tenant},
		},
		"status": map[string]any{"phase": "Active"},
	}}
	client := fake.NewSimpleDynamicClient(runtime.NewScheme(), namespace)
	forbidden := apierrors.NewForbidden(schema.GroupResource{Resource: "persistentvolumeclaims"}, "data",
		errors.New(`User "system:serviceaccount:ani:res" cannot get resource "persistentvolumeclaims"`))
	client.PrependReactor("get", "persistentvolumeclaims", func(ktesting.Action) (bool, runtime.Object, error) {
		return true, nil, forbidden
	})
	adapter, err := NewKubernetes(client)
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	usecase, err := biz.NewUseCase(adapter, nil, biz.Config{
		NamespacePrefix: "tenant-", ClusterID: tenant, Logger: slog.New(slog.NewJSONHandler(&out, nil)),
	})
	if err != nil {
		t.Fatal(err)
	}
	_, getErr := usecase.GetVolume(context.Background(), tenant, "data")
	if !errors.Is(getErr, biz.ErrAccessDenied) {
		t.Fatal("public classification must stay ErrAccessDenied", getErr)
	}
	logs := out.String()
	if !strings.Contains(logs, `"error_source":"kubernetes"`) || !strings.Contains(logs, `"error_code":"Forbidden"`) {
		t.Fatal("detail log lost the native Kubernetes cause", logs)
	}
	var statusErr *apierrors.StatusError
	if !errors.As(getErr, &statusErr) {
		t.Fatal("classified error lost the unwrap-able native cause")
	}
}
