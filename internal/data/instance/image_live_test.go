//go:build instancelive

package instance

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	imagebiz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/image"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	imagedata "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/image"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/remotecommand"
)

// This separate CPU probe consumes an image registered through the production
// operator and real encrypted tenant pull material. It never creates a GPU
// plan, accepted instance, quota operation, or product completion receipt.
func TestRuntimeImagePrivateHarborPullLive(t *testing.T) {
	path := os.Getenv("INSTANCE_IMAGE_LIVE_CONFIG")
	if path == "" {
		t.Skip("explicit owned live image experiment required")
	}
	var c struct {
		Directory, TenantID, Namespace, NamespaceUID, ClusterUID, DriverKubeconfig, ExecKubeconfig string
		ImageID, Reference, Digest, Notebook, PVC, StorageClass, Secret                            string
		DatabaseDSNFile, EncryptionKeysFile, HarborURL, HarborCAFile, HarborPasswordFile           string
	}
	body, e := os.ReadFile(path)
	if e != nil {
		t.Fatal("private experiment unavailable")
	}
	if e = json.Unmarshal(body, &c); e != nil {
		t.Fatal(e)
	}
	if c.Namespace != "ani-tenant-"+c.TenantID || !biz.UUID(c.TenantID) || !strings.HasPrefix(c.Notebook, "image-probe-") || !filepath.IsAbs(c.Directory) {
		t.Fatal("invalid scope")
	}
	if _, e = imagebiz.ParseImageID(c.ImageID); e != nil {
		t.Fatal("invalid Image identifier")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 7*time.Minute)
	defer cancel()
	dsn, e := os.ReadFile(c.DatabaseDSNFile)
	if e != nil {
		t.Fatal("Image DSN unavailable")
	}
	defer clear(dsn)
	repo, e := imagedata.OpenPostgres(ctx, strings.TrimSpace(string(dsn)))
	if e != nil {
		t.Fatal(e)
	}
	defer repo.Close()
	var keys struct {
		ActiveKeyID string            `json:"active_key_id"`
		Keys        map[string][]byte `json:"keys"`
	}
	body, e = os.ReadFile(c.EncryptionKeysFile)
	if e != nil {
		t.Fatal("keyring unavailable")
	}
	if e = json.Unmarshal(body, &keys); e != nil {
		t.Fatal(e)
	}
	clear(body)
	defer func() {
		for _, key := range keys.Keys {
			clear(key)
		}
	}()
	cipher, e := imagedata.NewAESGCMKeyring(keys.ActiveKeyID, keys.Keys)
	if e != nil {
		t.Fatal(e)
	}
	password, e := os.ReadFile(c.HarborPasswordFile)
	if e != nil {
		t.Fatal("registry identity unavailable")
	}
	defer clear(password)
	ca, e := os.ReadFile(c.HarborCAFile)
	if e != nil {
		t.Fatal(e)
	}
	harbor, e := imagedata.NewHarbor(imagedata.HarborConfig{URL: c.HarborURL, Username: "admin", Password: password, CAPEM: ca, RobotNamePrefix: "robot$", Timeout: 15 * time.Second})
	if e != nil {
		t.Fatal(e)
	}
	defer harbor.Close()
	runtime, e := imagebiz.NewRuntime(repo, repo, repo, harbor, cipher, time.Now)
	if e != nil {
		t.Fatal(e)
	}
	resolved, e := (Image{Runtime: runtime}).Resolve(ctx, c.TenantID, c.ImageID, "platform")
	if e != nil {
		t.Fatal(e)
	}
	if resolved.Reference != c.Reference || resolved.Digest != c.Digest {
		t.Fatal("immutable image mismatch")
	}
	cfg, e := clientcmd.BuildConfigFromFlags("", c.DriverKubeconfig)
	if e != nil {
		t.Fatal(e)
	}
	if cfg.Insecure || cfg.Host != "https://172.16.101.10:6443" {
		t.Fatal("unexpected cluster")
	}
	client, e := dynamic.NewForConfig(cfg)
	if e != nil {
		t.Fatal(e)
	}
	nsgvr := schema.GroupVersionResource{Version: "v1", Resource: "namespaces"}
	ns, e := client.Resource(nsgvr).Get(ctx, c.Namespace, metav1.GetOptions{})
	if e != nil || string(ns.GetUID()) != c.NamespaceUID || ns.GetLabels()["instance.ani.io/task"] != "instance-mvp-v15" || ns.GetLabels()["network.ani.io/tenant-id"] != c.TenantID {
		t.Fatal("namespace identity mismatch")
	}
	cluster, e := client.Resource(nsgvr).Get(ctx, "kube-system", metav1.GetOptions{})
	if e != nil || string(cluster.GetUID()) != c.ClusterUID {
		t.Fatal("cluster identity mismatch")
	}
	n, e := NewNotebook(client)
	if e != nil {
		t.Fatal(e)
	}
	if e = n.ConfigureImages(runtime); e != nil {
		t.Fatal(e)
	}
	i := biz.Instance{TenantID: c.TenantID, Spec: biz.Spec{Namespace: c.Namespace, ImagePullSecret: c.Secret}}
	if e = n.ensurePullSecret(ctx, i); e != nil {
		t.Fatal(e)
	}
	secret, e := client.Resource(schema.GroupVersionResource{Version: "v1", Resource: "secrets"}).Namespace(c.Namespace).Get(ctx, c.Secret, metav1.GetOptions{})
	if e != nil || secret.GetLabels()["image.ani.io/tenant-id"] != c.TenantID {
		t.Fatal("managed secret identity mismatch")
	}
	// The tenant-wide pull Secret is preserved; only probe compute/PVC are deleted.
	pvcgvr := schema.GroupVersionResource{Version: "v1", Resource: "persistentvolumeclaims"}
	nbgvr := schema.GroupVersionResource{Group: "kubeflow.org", Version: "v1", Resource: "notebooks"}
	podgvr := schema.GroupVersionResource{Version: "v1", Resource: "pods"}
	pvgvr := schema.GroupVersionResource{Version: "v1", Resource: "persistentvolumes"}
	labels := map[string]any{"instance.ani.io/task": "instance-mvp-v15", "instance.ani.io/image-probe": c.Notebook}
	pvc, e := client.Resource(pvcgvr).Namespace(c.Namespace).Create(ctx, &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": map[string]any{"name": c.PVC, "labels": labels}, "spec": map[string]any{"storageClassName": c.StorageClass, "accessModes": []any{"ReadWriteOnce"}, "resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}}}}, metav1.CreateOptions{})
	if e != nil {
		t.Fatal(e)
	}
	pvcUID := pvc.GetUID()
	var nbUID types.UID
	deleteOwned := func(ctx context.Context, gvr schema.GroupVersionResource, name string, uid types.UID) error {
		foreground := metav1.DeletePropagationForeground
		return client.Resource(gvr).Namespace(c.Namespace).Delete(ctx, name, metav1.DeleteOptions{PropagationPolicy: &foreground, Preconditions: &metav1.Preconditions{UID: &uid}})
	}
	t.Cleanup(func() {
		clean, done := context.WithTimeout(context.Background(), 20*time.Second)
		defer done()
		if nbUID != "" {
			_ = deleteOwned(clean, nbgvr, c.Notebook, nbUID)
		}
		_ = deleteOwned(clean, pvcgvr, c.PVC, pvcUID)
	})
	podspec := map[string]any{"serviceAccountName": "workload", "automountServiceAccountToken": false, "imagePullSecrets": []any{map[string]any{"name": c.Secret}}, "securityContext": map[string]any{"runAsUser": int64(1000), "runAsGroup": int64(1000), "fsGroup": int64(1000)}, "containers": []any{map[string]any{"name": "workspace", "image": resolved.Reference, "imagePullPolicy": "Always", "workingDir": "/workspace", "resources": map[string]any{"requests": map[string]any{"cpu": "100m", "memory": "256Mi"}, "limits": map[string]any{"cpu": "1", "memory": "1Gi"}}, "volumeMounts": []any{map[string]any{"name": "workspace", "mountPath": "/workspace"}}}}, "volumes": []any{map[string]any{"name": "workspace", "persistentVolumeClaim": map[string]any{"claimName": c.PVC}}}}
	// No command/args override: verify the actual image's lasting default entry.
	nb, e := client.Resource(nbgvr).Namespace(c.Namespace).Create(ctx, &unstructured.Unstructured{Object: map[string]any{"apiVersion": "kubeflow.org/v1", "kind": "Notebook", "metadata": map[string]any{"name": c.Notebook, "namespace": c.Namespace, "labels": labels}, "spec": map[string]any{"template": map[string]any{"spec": podspec}}}}, metav1.CreateOptions{})
	if e != nil {
		t.Fatal(e)
	}
	nbUID = nb.GetUID()
	poll := func(name string, fn func() bool) {
		t.Helper()
		end := time.Now().Add(2 * time.Minute)
		for time.Now().Before(end) {
			if fn() {
				return
			}
			select {
			case <-ctx.Done():
				t.Fatal(name, "cancelled")
			case <-time.After(time.Second):
			}
		}
		t.Fatal(name, "timeout")
	}
	var pod *unstructured.Unstructured
	poll("actual private image start", func() bool {
		list, e := client.Resource(podgvr).Namespace(c.Namespace).List(ctx, metav1.ListOptions{LabelSelector: "instance.ani.io/image-probe=" + c.Notebook})
		if e != nil || len(list.Items) != 1 {
			return false
		}
		p := &list.Items[0]
		statuses, _, _ := unstructured.NestedSlice(p.Object, "status", "containerStatuses")
		if len(statuses) != 1 {
			return false
		}
		st := statuses[0].(map[string]any)
		if st["ready"] != true || !strings.HasSuffix(st["imageID"].(string), c.Digest) {
			return false
		}
		pod = p
		return true
	})
	execcfg, e := clientcmd.BuildConfigFromFlags("", c.ExecKubeconfig)
	if e != nil || execcfg.Insecure || execcfg.Host != cfg.Host {
		t.Fatal("scoped exec config mismatch")
	}
	core, e := kubernetes.NewForConfig(execcfg)
	if e != nil {
		t.Fatal(e)
	}
	probe := "import json,os,pathlib,importlib.util; p=pathlib.Path('/workspace/image-contract-marker'); p.write_text('image-contract'); s=importlib.util.find_spec('torch'); print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'cwd':os.getcwd(),'workspace_write_read':p.read_text()=='image-contract','torch_present':s is not None,'cuda_directory':pathlib.Path('/usr/local/cuda').exists()}))"
	request := core.CoreV1().RESTClient().Post().Resource("pods").Name(pod.GetName()).Namespace(c.Namespace).SubResource("exec").VersionedParams(&corev1.PodExecOptions{Container: "workspace", Command: []string{"/bin/bash", "-lc", "python -c " + shellLiteral(probe)}, Stdout: true, Stderr: true}, scheme.ParameterCodec)
	executor, e := remotecommand.NewSPDYExecutor(execcfg, "POST", request.URL())
	if e != nil {
		t.Fatal(e)
	}
	var stdout, stderr bytes.Buffer
	execctx, done := context.WithTimeout(ctx, 30*time.Second)
	e = executor.StreamWithContext(execctx, remotecommand.StreamOptions{Stdout: &stdout, Stderr: &stderr})
	done()
	if e != nil {
		t.Fatal("scoped shell probe failed", e)
	}
	var result map[string]any
	if e = json.Unmarshal(stdout.Bytes(), &result); e != nil {
		t.Fatal("image probe result invalid")
	}
	if result["uid"] != float64(1000) || result["gid"] != float64(1000) || result["cwd"] != "/workspace" || result["workspace_write_read"] != true {
		t.Fatal("runtime contract mismatch")
	}
	// A short second observation proves default entry survives exec completion.
	select {
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	case <-time.After(3 * time.Second):
	}
	current, e := client.Resource(podgvr).Namespace(c.Namespace).Get(ctx, pod.GetName(), metav1.GetOptions{})
	if e != nil || current.GetUID() != pod.GetUID() {
		t.Fatal("entry did not persist")
	}
	statuses, _, _ := unstructured.NestedSlice(current.Object, "status", "containerStatuses")
	if len(statuses) != 1 || statuses[0].(map[string]any)["ready"] != true || statuses[0].(map[string]any)["restartCount"] != int64(0) {
		t.Fatal("default entry stopped or restarted")
	}
	pvc, e = client.Resource(pvcgvr).Namespace(c.Namespace).Get(ctx, c.PVC, metav1.GetOptions{})
	if e != nil || pvc.GetUID() != pvcUID {
		t.Fatal("workspace identity mismatch")
	}
	pvname, _, _ := unstructured.NestedString(pvc.Object, "spec", "volumeName")
	if pvname == "" {
		t.Fatal("workspace unbound")
	}
	pv, e := client.Resource(pvgvr).Get(ctx, pvname, metav1.GetOptions{})
	if e != nil {
		t.Fatal(e)
	}
	reclaim, _, _ := unstructured.NestedString(pv.Object, "spec", "persistentVolumeReclaimPolicy")
	claimuid, _, _ := unstructured.NestedString(pv.Object, "spec", "claimRef", "uid")
	if reclaim != "Delete" || claimuid != string(pvcUID) {
		t.Fatal("workspace reclamation scope mismatch")
	}
	if e = deleteOwned(ctx, nbgvr, c.Notebook, nbUID); e != nil {
		t.Fatal(e)
	}
	poll("compute reclaim", func() bool {
		_, e := client.Resource(nbgvr).Namespace(c.Namespace).Get(ctx, c.Notebook, metav1.GetOptions{})
		if !apierrors.IsNotFound(e) {
			return false
		}
		list, e := client.Resource(podgvr).Namespace(c.Namespace).List(ctx, metav1.ListOptions{LabelSelector: "instance.ani.io/image-probe=" + c.Notebook})
		return e == nil && len(list.Items) == 0
	})
	if e = deleteOwned(ctx, pvcgvr, c.PVC, pvcUID); e != nil {
		t.Fatal(e)
	}
	poll("volume reclaim", func() bool {
		_, pe := client.Resource(pvcgvr).Namespace(c.Namespace).Get(ctx, c.PVC, metav1.GetOptions{})
		_, ve := client.Resource(pvgvr).Get(ctx, pvname, metav1.GetOptions{})
		return apierrors.IsNotFound(pe) && apierrors.IsNotFound(ve)
	})
	result["image_id"] = c.ImageID
	result["reference"] = resolved.Reference
	result["digest"] = resolved.Digest
	result["notebook_uid"] = string(nbUID)
	result["pod_uid"] = string(pod.GetUID())
	result["pvc_uid"] = string(pvcUID)
	result["pv_uid"] = string(pv.GetUID())
	result["default_entry_survived"] = true
	result["managed_pull_secret_retained"] = c.Secret
	result["compute_and_volume_reclaimed"] = true
	result["gpu_runtime"] = "not_verified"
	body, e = json.MarshalIndent(result, "", "  ")
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(c.Directory, "image-live-result.json"), body, 0600); e != nil {
		t.Fatal(e)
	}
}

func shellLiteral(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'" }
