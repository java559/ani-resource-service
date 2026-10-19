package instancev1

import (
	"bytes"
	"fmt"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"testing"
)

func TestCurrentConfigurationRejectsRetiredVersions(t *testing.T) {
	for _, version := range []uint32{0, 1} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			spec := currentBusiness()
			spec.InstanceContractVersion = version
			spec.WorkspaceMountPath = ""
			if err := ValidateCreateSpec(spec, false); err == nil {
				t.Error("retired configuration accepted by create validation")
			}
			if _, err := CommandDigest(&CommandContext{}, spec); err == nil {
				t.Error("retired configuration produced a command digest")
			}
			if _, err := CanonicalBusinessPayload(spec); err == nil {
				t.Error("retired frozen payload was projected")
			}
			raw, err := protojson.Marshal(spec)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := DecodeBusinessPayload(raw); err == nil {
				t.Error("retired frozen payload was decoded")
			}
			if _, err := CanonicalBusinessPayloadVersion(&CreateInstanceData{Name: "workspace"}, version); err == nil {
				t.Error("retired input projection accepted")
			}
		})
	}
	current := currentBusiness()
	current.InstanceContractVersion = 2
	current.WorkspaceMountPath = "/workspace"
	raw, err := CanonicalBusinessPayload(current)
	if err != nil {
		t.Fatal(err)
	}
	restored, err := DecodeBusinessPayload(raw)
	if err != nil || !proto.Equal(current, restored) {
		t.Fatal("current frozen configuration did not round trip", err)
	}
}
func TestCurrentConfigurationInputDefaultsUseCurrentProjection(t *testing.T) {
	input := &CreateInstanceData{Name: "workspace"}
	actual, err := CanonicalBusinessPayload(input)
	if err != nil {
		t.Fatal(err)
	}
	current, err := CanonicalBusinessPayloadVersion(input, 2)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, current) {
		t.Fatal("default input still used a retired projection")
	}
}
