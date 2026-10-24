package vm

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic/fake"
	ktesting "k8s.io/client-go/testing"
)

func TestNativeNoCloudUsesOneOwnedUserAndDHCPSecret(t *testing.T) {
	v := biz.VM{TenantID: uuid.NewString(), ID: uuid.NewString(), Spec: biz.Spec{Namespace: "network-init", NamespaceUID: uuid.NewString(), Digest: strings.Repeat("e", 64), Media: biz.Source{Kind: "DISK_IMAGE", RootBus: "virtio", NICModel: "virtio"}, GuestInitialization: &biz.GuestInitialization{Method: "CLOUD_INIT_NOCLOUD", Username: "aniuser", PasswordSet: true, CredentialIntentDigest: strings.Repeat("a", 64)}}}
	v.Native = biz.ObjectRef{Kind: "VirtualMachine", Namespace: v.Spec.Namespace, Name: "vm-" + v.ID}
	v.Disks = []biz.Disk{biz.NewInitialDisk(v.ID, "system", "system", "system", 12)}
	ns := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "Namespace", "metadata": map[string]any{"name": v.Spec.Namespace, "uid": v.Spec.NamespaceUID, "labels": map[string]any{"network.ani.io/tenant-id": v.TenantID, "network.ani.io/managed-by": "ani-network-service"}}}}
	client := fake.NewSimpleDynamicClient(runtime.NewScheme(), ns)
	client.PrependReactor("create", "virtualmachines", func(a ktesting.Action) (bool, runtime.Object, error) {
		a.(ktesting.CreateAction).GetObject().(*unstructured.Unstructured).SetUID(types.UID(uuid.NewString()))
		return false, nil, nil
	})
	n := NewNativeWithClients(client, nil)
	ref, e := n.EnsureVM(context.Background(), v)
	if e != nil {
		t.Fatal(e)
	}
	vm, e := client.Resource(vmResource).Namespace(v.Spec.Namespace).Get(context.Background(), ref.Name, metav1.GetOptions{})
	if e != nil {
		t.Fatal(e)
	}
	volumes, _, _ := unstructured.NestedSlice(vm.Object, "spec", "template", "spec", "volumes")
	count := 0
	for _, value := range volumes {
		volume := value.(map[string]any)
		if volume["name"] != "guest-initialization" {
			continue
		}
		count++
		user, _, _ := unstructured.NestedString(volume, "cloudInitNoCloud", "secretRef", "name")
		network, _, _ := unstructured.NestedString(volume, "cloudInitNoCloud", "networkDataSecretRef", "name")
		if user != initializationSecretName(v) || network != user {
			t.Fatal("native cloud-init lacks its bounded DHCP secret reference")
		}
		if _, ok := volume["userData"]; ok {
			t.Fatal("plaintext initialization data in VM template")
		}
	}
	if count != 1 {
		t.Fatalf("expected one initialization device, got%d", count)
	}
	id, _, _ := unstructured.NestedString(vm.Object, "spec", "template", "spec", "domain", "firmware", "uuid")
	if id != v.ID {
		t.Fatal("DHCP support changed stable NoCloud identity")
	}
}
