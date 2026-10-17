package instancev1

import (
	"strings"
	"testing"
)

func TestManagedRuntimeClassOptionalCanonicalAndCPURejection(t *testing.T) {
	b := currentBusiness()
	raw, err := CanonicalBusinessPayload(b)
	if err != nil || strings.Contains(string(raw), "gpu_runtime_class_name") {
		t.Fatal("absent optional runtime changed old business representation", string(raw), err)
	}
	value := "nvidia"
	b.Spec.GpuRuntimeClassName = &value
	if ValidateCreateBusiness(b) == nil {
		t.Fatal("CPU accepted a GPU RuntimeClass")
	}
	for _, name := range []string{"", "Nvidia", "nvidia/runtime", "-nvidia", "nvidia.", strings.Repeat("a", 64)} {
		if ValidateGPURuntimeClassName(name) == nil {
			t.Fatal("invalid RuntimeClass accepted", name)
		}
	}
}
