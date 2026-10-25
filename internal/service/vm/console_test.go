package vm

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

type consoleOnlyNative struct {
	biz.NativePort
	full, identity int
}

func (n *consoleOnlyNative) Observe(context.Context, biz.VM) (biz.Observation, error) {
	n.full++
	return biz.Observation{}, biz.ErrDependency
}
func (n *consoleOnlyNative) ObserveConsole(_ context.Context, v biz.VM) (biz.Observation, error) {
	n.identity++
	return biz.Observation{VM: v.Native, Running: true, VMI: &biz.ObjectRef{Kind: "VirtualMachineInstance", Namespace: v.Spec.Namespace, Name: v.Native.Name, UID: "vmi-uid"}}, nil
}

type consoleOnlyEnvironment struct{ biz.EnvironmentPort }

func (e consoleOnlyEnvironment) ResolveNamespace(_ context.Context, _, name string) (biz.ObjectRef, error) {
	return biz.ObjectRef{Name: name, UID: "namespace-uid"}, nil
}

func TestConsoleUsesRuntimeIdentityAndKeepsVersionClosingFences(t *testing.T) {
	for _, mode := range []string{"running", "version", "closing"} {
		t.Run(mode, func(t *testing.T) {
			v := biz.VM{TenantID: uuid.NewString(), ID: uuid.NewString(), Version: 8, Spec: biz.Spec{Namespace: "tenant-console", NamespaceUID: "namespace-uid"}, Native: biz.ObjectRef{Kind: "VirtualMachine", Namespace: "tenant-console", Name: "vm-current", UID: "vm-uid"}}
			version := v.Version
			if mode == "version" {
				version--
			}
			v.Closing = mode == "closing"
			r := &snapshotServiceRepo{current: v}
			n := &consoleOnlyNative{}
			s := &Service{repo: r, native: n, environment: consoleOnlyEnvironment{}}
			binding, err := s.resolveConsole(context.Background(), v.TenantID, v.ID, version)
			if mode != "running" {
				if !errors.Is(err, biz.ErrConsole) || n.identity != 0 || n.full != 0 {
					t.Fatalf("version/closing fence bypassed: %v %+v", err, n)
				}
				return
			}
			if err != nil || binding.VmUid != v.Native.UID || binding.VmiUid != "vmi-uid" || n.identity != 1 || n.full != 0 || r.reads != 2 {
				t.Fatalf("console read unrelated storage or lost second version check: %v %+v", err, n)
			}
		})
	}
}
