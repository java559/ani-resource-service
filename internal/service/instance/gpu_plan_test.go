package instance

import (
	"encoding/json"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"os"
	"testing"
)

func builderPlans(t *testing.T) map[string]*acc.ResolvedGpuPlan {
	t.Helper()
	raw, e := os.ReadFile("../../../api/instance/v1/testdata/gpu-plan-vectors.json")
	if e != nil {
		t.Fatal(e)
	}
	var input map[string]json.RawMessage
	if e = json.Unmarshal(raw, &input); e != nil {
		t.Fatal(e)
	}
	out := map[string]*acc.ResolvedGpuPlan{}
	for name, raw := range input {
		p := new(acc.ResolvedGpuPlan)
		if e = protojson.Unmarshal(raw, p); e != nil {
			t.Fatal(e)
		}
		out[name] = p
	}
	return out
}
func TestGpuPlanBuilderQuantitiesAndTampering(t *testing.T) {
	plans := builderPlans(t)
	for name, p := range plans {
		t.Run(name, func(t *testing.T) {
			q, e := validateGpuPlan(p)
			if e != nil {
				t.Fatal("real ACC builder vector rejected", e)
			}
			want := int64(p.Request.Replicas) * int64(p.Request.DevicesPerReplica)
			code := "gpu.physical.count"
			if name == "Shared1" {
				want = 6144
				code = "gpu.shared_memory_mib"
			}
			if q.Code != code || q.Units != want {
				t.Fatal("wrong original charge", q)
			}
			bad := proto.Clone(p).(*acc.ResolvedGpuPlan)
			bad.Runtime.LimitsPerContainer[0].Value = "invalid"
			if _, e := validateGpuPlan(bad); e == nil {
				t.Fatal("runtime mutation accepted")
			}
		})
	}
	for name, mutate := range map[string]func(*acc.ResolvedGpuPlan){"whole request exceeds max": func(p *acc.ResolvedGpuPlan) { p.Profile.Spec.MaxDevicesPerReplica = p.Request.DevicesPerReplica - 1 }, "total mismatch": func(p *acc.ResolvedGpuPlan) { p.Totals.ExclusiveDeviceCount = 1 }, "number mismatch": func(p *acc.ResolvedGpuPlan) {
		for _, kv := range p.Runtime.LimitsPerContainer {
			if kv.Key == "volcano.sh/vgpu-number" {
				kv.Value = "1"
			}
		}
	}} {
		t.Run(name, func(t *testing.T) {
			p := proto.Clone(plans["WholeProfileMax"]).(*acc.ResolvedGpuPlan)
			mutate(p)
			var e error
			p.Profile.SpecDigest, e = gpuSpecDigest(p.Profile)
			if e != nil {
				t.Fatal(e)
			}
			p.ResolutionDigest, e = gpuPlanDigest(p)
			if e != nil {
				t.Fatal(e)
			}
			if _, e := validateGpuPlan(p); e == nil {
				t.Fatal("semantic mutation with correct digest accepted")
			}
		})
	}
}
