package vm

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strings"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
)

var secretResource = schema.GroupVersionResource{Version: "v1", Resource: "secrets"}

// The one managed Ethernet interface keeps DHCP across native VMI/MAC
// replacement. Cloud-init's fallback instead pins the first boot's MAC into
// netplan, which leaves the next VMI's interface down with a stable instance-id.
// This is bounded platform configuration, never caller-provided network data.
const guestNetworkData = `{"version":2,"ethernets":{"default":{"match":{"name":"e*"},"dhcp4":true}}}`

func initializationSecretName(v biz.VM) string { return "vm-init-" + v.ID }
func (n *Native) SetGuestCredentialReader(reader func(context.Context, biz.VM) (string, error)) {
	n.guestPasswordHash = reader
}
func validInitializationSecret(o *unstructured.Unstructured, v biz.VM) bool {
	if v.Spec.GuestInitialization == nil {
		return false
	}
	return matches(o, v) && owned(o, "VirtualMachine", v.Native.UID) && o.GetName() == initializationSecretName(v) && o.GetAnnotations()["vm.ani.io/initialization-intent"] == v.Spec.GuestInitialization.CredentialIntentDigest && (v.InitializationSecret.UID == "" || v.InitializationSecret.UID == string(o.GetUID()))
}
func cloudInitUserData(g *biz.GuestInitialization, passwordHash string) ([]byte, error) {
	if g == nil || g.Method != "CLOUD_INIT_NOCLOUD" || g.Username == "" || g.PasswordSet != (passwordHash != "") {
		return nil, biz.ErrInvalid
	}
	user := map[string]any{"name": g.Username, "shell": "/bin/bash", "lock_passwd": !g.PasswordSet, "sudo": "ALL=(ALL) ALL"}
	if passwordHash != "" {
		if !strings.HasPrefix(passwordHash, "$6$") {
			return nil, biz.ErrInvalid
		}
		user["hashed_passwd"] = passwordHash
	}
	if g.SSHPublicKey != "" {
		user["ssh_authorized_keys"] = []string{g.SSHPublicKey}
	}
	raw, e := json.Marshal(map[string]any{"users": []any{user}, "ssh_pwauth": false, "disable_root": true, "chpasswd": map[string]any{"expire": false}})
	if e != nil {
		return nil, e
	}
	return append([]byte("#cloud-config\n"), raw...), nil
}
func (n *Native) EnsureInitialization(ctx context.Context, v biz.VM) (biz.ObjectRef, error) {
	if v.Spec.GuestInitialization == nil || v.Native.UID == "" {
		return biz.ObjectRef{}, biz.ErrInvalid
	}
	if e := n.namespace(ctx, v); e != nil {
		return biz.ObjectRef{}, e
	}
	existing, e := n.client.Resource(secretResource).Namespace(v.Spec.Namespace).Get(ctx, initializationSecretName(v), metav1.GetOptions{})
	if e == nil {
		if !validInitializationSecret(existing, v) {
			return biz.ObjectRef{}, biz.ErrOwnership
		}
		return objectRef(existing), nil
	}
	if !apierrors.IsNotFound(e) {
		return biz.ObjectRef{}, e
	}
	if v.InitializationSecret.UID != "" {
		return biz.ObjectRef{}, biz.ErrOwnership
	}
	if n.guestPasswordHash == nil {
		return biz.ObjectRef{}, biz.ErrDependency
	}
	hash, e := n.guestPasswordHash(ctx, v)
	if e != nil {
		return biz.ObjectRef{}, e
	}
	userdata, e := cloudInitUserData(v.Spec.GuestInitialization, hash)
	if e != nil {
		return biz.ObjectRef{}, e
	}
	defer clear(userdata)
	secret := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "Secret", "metadata": map[string]any{"namespace": v.Spec.Namespace, "name": initializationSecretName(v)}, "immutable": true, "type": "Opaque", "data": map[string]any{"userdata": base64.StdEncoding.EncodeToString(userdata), "networkdata": base64.StdEncoding.EncodeToString([]byte(guestNetworkData))}}}
	secret.SetLabels(labelVM(v))
	secret.SetAnnotations(map[string]string{"vm.ani.io/initialization-intent": v.Spec.GuestInitialization.CredentialIntentDigest, "vm.ani.io/network-initialization": "dhcp-single-nic-v1"})
	secret.SetOwnerReferences(ownReference(v))
	created, e := n.client.Resource(secretResource).Namespace(v.Spec.Namespace).Create(ctx, secret, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(e) {
		return n.EnsureInitialization(ctx, v)
	}
	if e != nil {
		return biz.ObjectRef{}, e
	}
	return objectRef(created), nil
}
func (n *Native) DeleteInitialization(ctx context.Context, v biz.VM) (bool, error) {
	o, e := n.client.Resource(secretResource).Namespace(v.Spec.Namespace).Get(ctx, initializationSecretName(v), metav1.GetOptions{})
	if apierrors.IsNotFound(e) {
		return true, nil
	}
	if e != nil {
		return false, e
	}
	if !validInitializationSecret(o, v) {
		return false, biz.ErrOwnership
	}
	uid := types.UID(o.GetUID())
	e = n.client.Resource(secretResource).Namespace(v.Spec.Namespace).Delete(ctx, o.GetName(), metav1.DeleteOptions{Preconditions: &metav1.Preconditions{UID: &uid}})
	if e != nil && !apierrors.IsNotFound(e) {
		return false, e
	}
	return false, nil
}
