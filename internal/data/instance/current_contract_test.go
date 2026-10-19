package instance

import (
	"encoding/json"
	"fmt"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"testing"
)

func TestCurrentConfigurationRejectsRetiredSnapshotsAndNotebook(t *testing.T) {
	_, current, _, _ := diagnosticFixture(t, false)
	for _, version := range []uint32{0, 1} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			instance := current
			instance.Spec.ContractVersion = version
			instance.Spec.WorkspaceMountPath = ""
			if _, err := RenderNotebook(instance); err == nil {
				t.Error("retired configuration rendered a Notebook")
			}
			for _, hash := range []string{instance.CreateRequestHash, ""} {
				instance.CreateRequestHash = hash
				raw, err := json.Marshal(instance)
				if err != nil {
					t.Fatal(err)
				}
				var restored biz.Instance
				if err := decodeSnapshot(raw, &restored); err == nil {
					t.Error("retired configuration decoded from persisted snapshot")
				}
			}
		})
	}
}
