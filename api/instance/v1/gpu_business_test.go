package instancev1

import (
	"encoding/json"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"os"
	"strings"
	"testing"
)

func TestFrozenGpuReferenceAndSummaryPlanAgreement(t *testing.T) {
	raw, e := os.ReadFile("testdata/gpu-plan-vectors.json")
	if e != nil {
		t.Fatal(e)
	}
	var vectors map[string]json.RawMessage
	if e = json.Unmarshal(raw, &vectors); e != nil {
		t.Fatal(e)
	}
	for name, raw := range vectors {
		t.Run(name, func(t *testing.T) {
			p := new(acc.ResolvedGpuPlan)
			if e := protojson.Unmarshal(raw, p); e != nil {
				t.Fatal(e)
			}
			b := currentBusiness()
			b.Gpu = proto.Clone(p.Request).(*acc.GpuRequest)
			f := b.Spec
			f.ComputeType = "gpu"
			f.ClusterId = p.Request.ClusterId
			digest := p.Profile.SpecDigest
			f.GpuSpecDigest = &digest
			s := p.Profile.Spec
			f.GpuSummary = &GpuSummary{ProfileId: p.Request.ProfileId, ProfileVersion: p.Request.ProfileVersion, Mode: s.Mode, ModelKey: s.ModelKey, DevicesPerInstance: p.Request.DevicesPerReplica, CoreLimitPercent: s.CoreLimitPercent}
			if s.Mode == acc.SupplyMode_SHARED_FIXED {
				m, total := s.SharedMemoryMib, p.Totals.SharedMemoryMib
				f.GpuSummary.SharedMemoryMibPerDevice = &m
				f.GpuSummary.SharedMemoryMibTotal = &total
			}
			if e := ValidateGpuBusinessPlan(b, p); e != nil {
				t.Fatal("builder plan rejected", e)
			}
			for label, change := range map[string]func(*CreateBusiness){"missing digest": func(b *CreateBusiness) { b.Spec.GpuSpecDigest = nil }, "different digest": func(b *CreateBusiness) { v := strings.Repeat("e", 64); b.Spec.GpuSpecDigest = &v }, "model": func(b *CreateBusiness) { b.Spec.GpuSummary.ModelKey = "other" }, "core": func(b *CreateBusiness) { b.Spec.GpuSummary.CoreLimitPercent++ }, "count": func(b *CreateBusiness) { b.Spec.GpuSummary.DevicesPerInstance++ }} {
				t.Run(label, func(t *testing.T) {
					changed := proto.Clone(b).(*CreateBusiness)
					change(changed)
					if _, e := BusinessPayloadDigest(changed); e != nil {
						t.Fatal(e)
					}
					if ValidateGpuBusinessPlan(changed, p) == nil {
						t.Fatal("correct digest authorized conflicting facts")
					}
				})
			}
		})
	}
	cpu := currentBusiness()
	if e := ValidateGpuBusinessPlan(cpu, nil); e != nil {
		t.Fatal(e)
	}
	if ValidateGpuBusinessPlan(cpu, &acc.ResolvedGpuPlan{}) == nil {
		t.Fatal("CPU acquired GPU plan")
	}
}
