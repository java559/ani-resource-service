package instancev1

import (
	"errors"
	"google.golang.org/protobuf/proto"
	"strings"
	"testing"
)

func currentBusiness() *CreateBusiness {
	digest := "sha256:" + strings.Repeat("a", 64)
	return &CreateBusiness{Data: &CreateInstanceData{Name: "workspace-a", ImageId: "img_" + strings.Repeat("b", 32), ImageScope: "platform", PresetId: "20000000-0000-4000-8000-000000000001"}, Spec: &FrozenSpec{InstanceKind: "container", ComputeType: "cpu", ImageReference: "registry.example/tenant/work@" + digest, ImageDigest: digest, Cpu: "1000m", Memory: "2048Mi", ClusterId: "b90dc57f-81ad-44ed-8088-6d731b63adea", Namespace: "tenant-11111111-1111-4111-8111-111111111111", ServiceAccount: "workload", StorageClass: "ani-block", WorkspaceSize: "1Gi", Shell: "/bin/sh", Uid: 1000, Gid: 1000, Scheduling: &SchedulingOptions{PodPlacement: SchedulingOptions_NONE.Enum()}, PresetName: "CPU", PresetCode: "cpu-small"}}
}
func TestCanonicalCurrentContractAndPresence(t *testing.T) {
	b := currentBusiness()
	raw, e := CanonicalBusinessPayload(b)
	if e != nil {
		t.Fatal(e)
	}
	decoded, e := DecodeBusinessPayload(raw)
	if e != nil || !proto.Equal(b, decoded) {
		t.Fatal("current contract round trip", e)
	}
	rawMissing, e := BusinessPayloadDigest(b.Data)
	if e != nil {
		t.Fatal(e)
	}
	b.Data.Scheduling = &SchedulingOptions{PodPlacement: SchedulingOptions_NONE.Enum()}
	rawExplicit, e := BusinessPayloadDigest(b.Data)
	if e != nil || rawMissing == rawExplicit {
		t.Fatal("missing intent and explicit NONE lost presence", e)
	}
	for _, old := range []string{`{"data":{"preset_version":"1"}}`, `{"spec":{"instance_type":"cpu"}}`} {
		if _, e := DecodeBusinessPayload([]byte(old)); e == nil {
			t.Fatal("old field accepted")
		}
	}
	if strings.Contains(string(raw), "instance_type") || strings.Contains(string(raw), "preset_version") {
		t.Fatal("old fields remained")
	}
}
func TestSchedulingConsistency(t *testing.T) {
	b := currentBusiness()
	for name, mutate := range map[string]func(*CreateBusiness){
		"missing kind":      func(b *CreateBusiness) { b.Spec.InstanceKind = "" },
		"missing type":      func(b *CreateBusiness) { b.Spec.ComputeType = "" },
		"missing effective": func(b *CreateBusiness) { b.Spec.Scheduling = nil },
		"missing placement": func(b *CreateBusiness) { b.Spec.Scheduling = &SchedulingOptions{} },
		"explicit mismatch": func(b *CreateBusiness) {
			b.Data.Scheduling = &SchedulingOptions{PodPlacement: SchedulingOptions_SPREAD_PREFERRED.Enum()}
		},
	} {
		t.Run(name, func(t *testing.T) {
			v := proto.Clone(b).(*CreateBusiness)
			mutate(v)
			if ValidateCreateBusiness(v) == nil {
				t.Fatal("invalid frozen business accepted")
			}
		})
	}
	b.Data.Scheduling = &SchedulingOptions{PodPlacement: SchedulingOptions_SPREAD_PREFERRED.Enum()}
	if _, e := BusinessPayloadDigest(b); e != nil {
		t.Fatal(e)
	}
	if e := ValidateCreateBusiness(b); !errors.Is(e, ErrSchedulingConflict) {
		t.Fatal("correct digest cannot authorize conflicting intent", e)
	}
}
func TestFrozenDigestCoversResourcesAndScheduling(t *testing.T) {
	b := currentBusiness()
	original, e := BusinessPayloadDigest(b)
	if e != nil {
		t.Fatal(e)
	}
	for name, mutate := range map[string]func(*CreateBusiness){"cpu": func(b *CreateBusiness) { b.Spec.Cpu = "2000m" }, "memory": func(b *CreateBusiness) { b.Spec.Memory = "4096Mi" }, "image": func(b *CreateBusiness) { b.Spec.ImageDigest = "sha256:changed" }, "preset name": func(b *CreateBusiness) { b.Spec.PresetName = "changed" }, "placement": func(b *CreateBusiness) { b.Spec.Scheduling.PodPlacement = SchedulingOptions_SPREAD_PREFERRED.Enum() }} {
		t.Run(name, func(t *testing.T) {
			v := proto.Clone(b).(*CreateBusiness)
			mutate(v)
			got, e := BusinessPayloadDigest(v)
			if e != nil || got == original {
				t.Fatal("frozen mutation not covered", e)
			}
		})
	}
}

func TestSharedCanonicalVector(t *testing.T) {
	b := currentBusiness()
	got, e := BusinessPayloadDigest(b)
	if e != nil || got != "49dd7b0fa157ee2f929b99bc512c37324d0ff411c7b966ad5b8091d55b6035e7" {
		t.Fatal("frozen cross repository digest", got, e)
	}
	raw, e := BusinessPayloadDigest(b.Data)
	if e != nil || raw != "cd47f6a66668157d8e32375f4b83c1c93a4d6c8a230ce4cff426b9d0460647af" {
		t.Fatal("raw data digest", raw, e)
	}
	b.Data.Scheduling = &SchedulingOptions{PodPlacement: SchedulingOptions_NONE.Enum()}
	explicit, e := BusinessPayloadDigest(b.Data)
	if e != nil || explicit != "2a8043a9a9d924cb387238228eb0e958a9e2fbfce65daca3e17a9c67651f4cd0" {
		t.Fatal("explicit NONE data digest", explicit, e)
	}
}

func TestReservedGrpcFieldsCannotDisappearInDigest(t *testing.T) {
	b := currentBusiness()
	// Deleted CreateInstanceData.preset_version tag 5 (varint), received by gRPC.
	b.Data.ProtoReflect().SetUnknown([]byte{0x28, 0x01})
	if _, e := CanonicalBusinessPayload(b); e == nil {
		t.Fatal("reserved nested field omitted from digest")
	}
	if e := ValidateCreateBusiness(b); e == nil {
		t.Fatal("reserved nested field accepted")
	}
}
