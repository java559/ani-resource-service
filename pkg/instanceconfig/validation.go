// Package instanceconfig validates immutable instance configuration using no
// transport or provider types. Both public hashing and domain admission use it.
package instanceconfig

import (
	"errors"
	"path"
	"regexp"
	"strings"
	"unicode/utf8"
)

var ErrInvalid = errors.New("INVALID_INSTANCE_REQUEST")
var ErrReserved = errors.New("INSTANCE_RESERVED_CONFIG")
var name = regexp.MustCompile(`^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$`)
var dns = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)
var envName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func ReservedKey(key string) bool {
	parts := strings.SplitN(key, "/", 2)
	if len(parts) == 2 {
		domain := parts[0]
		for _, d := range []string{"ani.io", "networking.kubercloud.com", "k8s.v1.cni.cncf.io", "ovn.kubernetes.io", "volcano.sh", "scheduling.volcano.sh", "statefulset.kubernetes.io"} {
			if domain == d || strings.HasSuffix(domain, "."+d) {
				return true
			}
		}
	}
	switch key {
	case "serving.kserve.io/inferenceservice", "jobset.sigs.k8s.io/jobset-name", "leaderworkerset.sigs.k8s.io/name", "kubeflow-resource-stopped", "notebook-name", "controller-revision-hash", "apps.kubernetes.io/pod-index", "scheduling.k8s.io/group-name":
		return true
	}
	return false
}
func ReservedEnv(key string) bool {
	for _, prefix := range []string{"ANI_", "NVIDIA_", "CUDA_", "HAMI_", "VGPU_"} {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	switch key {
	case "NB_PREFIX", "GPU_DEVICE_ORDINAL", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "LD_PRELOAD":
		return true
	}
	return false
}
func qualifiedKey(key string) bool {
	parts := strings.Split(key, "/")
	if len(parts) > 2 {
		return false
	}
	n := parts[len(parts)-1]
	if len(n) == 0 || len(n) > 63 || !name.MatchString(n) {
		return false
	}
	if len(parts) == 2 {
		if len(parts[0]) == 0 || len(parts[0]) > 253 {
			return false
		}
		for _, label := range strings.Split(parts[0], ".") {
			if len(label) > 63 || !dns.MatchString(label) {
				return false
			}
		}
	}
	return true
}
func MountPath(p string) bool {
	if p == "" || !utf8.ValidString(p) || strings.ContainsAny(p, "\x00\r\n") || len(p) > 4096 || !path.IsAbs(p) || path.Clean(p) != p || p == "/" {
		return false
	}
	for _, protected := range []string{"/bin", "/sbin", "/usr", "/etc", "/proc", "/sys", "/dev", "/var/run", "/run", "/lib", "/lib64"} {
		if p == protected || strings.HasPrefix(p, protected+"/") || strings.HasPrefix(protected, p+"/") {
			return false
		}
	}
	return true
}
func overlap(a, b string) bool {
	return a == b || strings.HasPrefix(a, b+"/") || strings.HasPrefix(b, a+"/")
}
func Validate(workspace string, mounts []string, labels, annotations map[string]string, names, values []string) error {
	if !MountPath(workspace) || len(mounts) > 8 || len(labels) > 32 || len(annotations) > 32 || len(names) > 64 || len(names) != len(values) {
		return ErrInvalid
	}
	paths := []string{workspace}
	for _, p := range mounts {
		if !MountPath(p) {
			return ErrInvalid
		}
		for _, old := range paths {
			if overlap(old, p) {
				return ErrInvalid
			}
		}
		paths = append(paths, p)
	}
	size := 0
	for index, m := range []map[string]string{labels, annotations} {
		for k, v := range m {
			if ReservedKey(k) {
				return ErrReserved
			}
			if !qualifiedKey(k) || !utf8.ValidString(v) || strings.ContainsRune(v, 0) {
				return ErrInvalid
			}
			if index == 0 && (len(v) > 63 || v != "" && !name.MatchString(v)) {
				return ErrInvalid
			}
			size += len(k) + len(v)
		}
	}
	if size > 16*1024 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	size = 0
	for i, n := range names {
		if ReservedEnv(n) {
			return ErrReserved
		}
		if !envName.MatchString(n) || seen[n] || !utf8.ValidString(values[i]) || strings.ContainsRune(values[i], 0) {
			return ErrInvalid
		}
		seen[n] = true
		size += len(values[i])
	}
	if size > 16*1024 {
		return ErrInvalid
	}
	return nil
}
