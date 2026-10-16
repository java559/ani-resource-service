//go:build instanceintegration

package instance_test

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	attachment "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/integration/v1"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	data "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/instance"
	service "github.com/zhangzhe-ctrl/ani-resource-service/internal/service/instance"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func TestGpuCompletePlanPersistenceOriginalChargesAndRendering(t *testing.T) {
	raw, e := os.ReadFile("../../../api/instance/v1/testdata/gpu-plan-vectors.json")
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
			f := database(t)
			admin := platformContext()
			tenant, id, operation := uuid.NewString(), uuid.NewString(), uuid.NewString()
			ctx := biz.WithCaller(context.Background(), biz.Caller{TenantID: tenant, Actor: "governance:user:42"})
			spec := p.Profile.Spec
			g := &biz.GPUReference{ProfileID: p.Request.ProfileId, ProfileVersion: p.Request.ProfileVersion, SpecDigest: p.Profile.SpecDigest, DevicesPerInstance: p.Request.DevicesPerReplica, ClusterID: p.Request.ClusterId, PoolID: p.Request.PoolId, Mode: spec.Mode.String(), ModelKey: spec.ModelKey, CoreLimitPercent: spec.CoreLimitPercent}
			if spec.Mode == acc.SupplyMode_SHARED_FIXED {
				m := spec.SharedMemoryMib
				g.SharedMemoryMiBPerDevice = &m
			}
			preset, e := f.repo.CreatePreset(admin, biz.Preset{Code: strings.ToLower(name), Name: name, ComputeType: biz.TypeGPU, CPUMillis: 1250, MemoryMiB: 1536, Enabled: true, GPU: g})
			if e != nil {
				t.Fatal(e)
			}
			s, e := service.NewService(f.repo, fixtureImage{}, fixtureEnvironment{}, service.Config{NamespacePrefix: "tenant-", ClusterID: p.Request.ClusterId, ServiceAccount: "workload", StorageClass: "ani-block", WorkspaceSize: "1Gi", Shell: "/bin/sh", UID: 1000, GID: 1000})
			if e != nil {
				t.Fatal(e)
			}
			b, e := s.ResolveCreateSpec(ctx, &pb.ResolveCreateSpecRequest{TenantId: tenant, Data: &pb.CreateInstanceData{Name: "gpu-work", ImageId: "img_" + strings.Repeat("a", 32), ImageScope: "platform", PresetId: preset.ID}})
			if e != nil {
				t.Fatal(e)
			}
			if !proto.Equal(b.Gpu, p.Request) || b.Spec.GetGpuSpecDigest() != p.Profile.SpecDigest {
				t.Fatal("database reference not frozen")
			}
			digest, e := pb.BusinessPayloadDigest(b)
			if e != nil {
				t.Fatal(e)
			}
			code, units := "gpu.physical.count", p.Totals.ExclusiveDeviceCount
			if spec.Mode == acc.SupplyMode_SHARED_FIXED {
				code, units = "gpu.shared_memory_mib", p.Totals.SharedMemoryMib
			}
			chargeID := uuid.NewString()
			gpuCharges := []*attachment.GpuChargeRef{{ChargeId: chargeID, QuotaCode: code, OriginalUnits: units}}
			full := []*pb.OriginalQuotaCharge{{ChargeId: chargeID, QuotaCode: code, OriginalUnits: units}}
			ref := &acc.GpuUsageRef{TenantId: tenant, OwnerService: biz.Owner, ResourceId: id, CreateOperationId: operation}
			request := &pb.AcceptCreateRequest{TenantId: tenant, OperationId: operation, Business: b, OriginalCharges: full, GpuOwnerAttachment: &attachment.GpuOwnerCreateAttachment{Ref: ref, Actor: &acc.Actor{Type: "user", Id: "42"}, RequestHash: strings.Repeat("a", 64), GpuPlan: p, GpuCharges: gpuCharges, BusinessPayloadDigest: digest, MeteringVersion: "gpu-metering-v1"}}
			if _, e = s.AcceptCreate(ctx, request); e != nil {
				t.Fatal("GPU persistent intake", e)
			}
			if _, e = s.AcceptCreate(ctx, request); e != nil {
				t.Fatal("GPU retry", e)
			}
			record, e := f.repo.Get(ctx, tenant, id)
			if e != nil {
				t.Fatal(e)
			}
			restored := new(acc.ResolvedGpuPlan)
			if e = protojson.Unmarshal(record.Spec.GPU.Plan, restored); e != nil || !proto.Equal(p, restored) {
				t.Fatal("full plan lost on PG round trip", e)
			}
			if len(record.Charges) != 1 || record.Charges[0].ID != chargeID || record.Charges[0].Units != units || record.Spec.GPUSpecDigest == nil || *record.Spec.GPUSpecDigest != p.Profile.SpecDigest {
				t.Fatal("original ledger/ref changed", record.Charges)
			}
			if e = f.repo.DeletePreset(admin, preset.ID); e != nil {
				t.Fatal(e)
			}
			notebook, e := data.RenderNotebook(record)
			if e != nil {
				t.Fatal(e)
			}
			containers, _, _ := unstructured.NestedSlice(notebook.Object, "spec", "template", "spec", "containers")
			if len(containers) != 1 {
				t.Fatal("GPU quantity became replicas")
			}
			limits, _, _ := unstructured.NestedStringMap(containers[0].(map[string]any), "resources", "limits")
			for _, kv := range restored.Runtime.LimitsPerContainer {
				if limits[kv.Key] != kv.Value {
					t.Fatal("render did not consume frozen runtime", kv.Key)
				}
			}
			deletion := uuid.NewString()
			del := &pb.AcceptDeleteRequest{TenantId: tenant, OperationId: deletion, ExpectedVersion: record.Version, OriginalCharges: full, GpuOwnerAttachment: &attachment.GpuOwnerDeleteAttachment{DeleteOperationId: deletion, Ref: ref, Actor: &acc.Actor{Type: "user", Id: "42"}, RequestHash: strings.Repeat("d", 64), OriginalGpuCharges: gpuCharges, OriginalGpuPlan: restored, MeteringVersion: "gpu-metering-v1"}}
			if _, e = s.AcceptDelete(ctx, del); e != nil {
				t.Fatal("original plan deletion", e)
			}
			after, e := f.repo.Get(ctx, tenant, id)
			if e != nil || after.Desired != biz.Deleted || len(after.Charges) != 1 || after.Charges[0].Units != units || after.Spec.GPU.Digest != p.ResolutionDigest {
				t.Fatal("delete lost original charge/runtime", e)
			}
		})
	}
}
