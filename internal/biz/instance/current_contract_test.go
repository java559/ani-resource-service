package instance

import "testing"

func TestCurrentConfigurationRejectsRetiredVersions(t *testing.T) {
	for _, version := range []uint32{0, 1} {
		if err := ValidateConfiguration(Spec{ContractVersion: version}); err == nil {
			t.Fatalf("retired configuration %d accepted", version)
		}
	}
	if err := ValidateConfiguration(Spec{ContractVersion: 2, WorkspaceMountPath: "/workspace"}); err != nil {
		t.Fatal("current configuration defaults rejected", err)
	}
}
