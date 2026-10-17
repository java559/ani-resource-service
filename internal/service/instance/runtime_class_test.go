package instance

import (
	"context"
	"strings"
	"testing"

	attachment "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/integration/v1"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"google.golang.org/protobuf/proto"
)

func TestGPURuntimeClassFrozenBeforeSignedIntake(t *testing.T) {
	const tenant = "11111111-1111-4111-8111-111111111111"
	const instance = "22222222-2222-4222-8222-222222222222"
	const operation = "33333333-3333-4333-8333-333333333333"
	for name, plan := range builderPlans(t) {
		if name != "Shared1" && name != "Whole1" {
			continue
		}
		t.Run(name, func(t *testing.T) {
			g := plan.Profile.Spec
			repo := &cpuIntake{preset: biz.Preset{ID: instance, Code: "gpu", Name: "GPU", ComputeType: biz.TypeGPU, CPUMillis: 1000, MemoryMiB: 2048, Enabled: true, GPU: &biz.GPUReference{ProfileID: plan.Request.ProfileId, ProfileVersion: plan.Request.ProfileVersion, SpecDigest: plan.Profile.SpecDigest, DevicesPerInstance: plan.Request.DevicesPerReplica, ClusterID: plan.Request.ClusterId, PoolID: plan.Request.PoolId, Mode: g.Mode.String(), ModelKey: g.ModelKey, CoreLimitPercent: g.CoreLimitPercent}}}
			if g.Mode == acc.SupplyMode_SHARED_FIXED {
				m := g.SharedMemoryMib
				repo.preset.GPU.SharedMemoryMiBPerDevice = &m
			}
			cfg := Config{NamespacePrefix: "tenant-", ClusterID: plan.Request.ClusterId, ServiceAccount: "workload", StorageClass: "ani-block", WorkspaceSize: "1Gi", Shell: "/bin/bash", UID: 1000, GID: 1000, GPURuntimeClassName: "nvidia"}
			s, err := NewService(repo, &cpuImageBoundary{}, &cpuEnvironmentBoundary{}, cfg)
			if err != nil {
				t.Fatal(err)
			}
			ctx := biz.WithCaller(context.Background(), biz.Caller{TenantID: tenant, Actor: "governance:user:42"})
			business, err := s.ResolveCreateSpec(ctx, &pb.ResolveCreateSpecRequest{TenantId: tenant, Data: &pb.CreateInstanceData{Name: "gpu-work", ImageId: "img_" + strings.Repeat("a", 32), ImageScope: "platform", PresetId: instance}})
			if err != nil || business.GetSpec().GetGpuRuntimeClassName() != "nvidia" {
				t.Fatal("managed GPU RuntimeClass was not frozen before signing", business, err)
			}
			digest, err := pb.BusinessPayloadDigest(business)
			if err != nil {
				t.Fatal(err)
			}
			charge, err := validateGpuPlan(plan)
			if err != nil {
				t.Fatal(err)
			}
			request := &pb.AcceptCreateRequest{TenantId: tenant, OperationId: operation, Business: business, OriginalCharges: []*pb.OriginalQuotaCharge{{ChargeId: instance, QuotaCode: charge.Code, OriginalUnits: charge.Units}}, GpuOwnerAttachment: &attachment.GpuOwnerCreateAttachment{Ref: &acc.GpuUsageRef{TenantId: tenant, OwnerService: biz.Owner, ResourceId: instance, CreateOperationId: operation}, Actor: &acc.Actor{Type: "user", Id: "42"}, RequestHash: strings.Repeat("a", 64), GpuPlan: plan, GpuCharges: []*attachment.GpuChargeRef{{ChargeId: instance, QuotaCode: charge.Code, OriginalUnits: charge.Units}}, BusinessPayloadDigest: digest, MeteringVersion: "gpu-metering-v1"}}
			// Intake/replay must use the signed snapshot after configuration changes.
			s.config.GPURuntimeClassName = "changed-runtime"
			if _, err = s.AcceptCreate(ctx, request); err != nil {
				t.Fatal(err)
			}
			if len(repo.creates) != 1 || repo.creates[0].Spec.GPU.RuntimeClass != "nvidia" || plan.Runtime.RuntimeClassName != "" {
				t.Fatal("intake lost frozen owner runtime or mutated the ACC plan", repo.creates)
			}
			changed := proto.Clone(request).(*pb.AcceptCreateRequest)
			value := "tampered-runtime"
			changed.Business.Spec.GpuRuntimeClassName = &value
			if _, err = s.AcceptCreate(ctx, changed); err == nil || len(repo.creates) != 1 {
				t.Fatal("runtime mutation bypassed the signed business digest", err)
			}
			// A provider-specified RuntimeClass remains authoritative.
			explicit := proto.Clone(request).(*pb.AcceptCreateRequest)
			explicit.GpuOwnerAttachment.GpuPlan.Runtime.RuntimeClassName = "provider-runtime"
			explicit.GpuOwnerAttachment.GpuPlan.ResolutionDigest, err = gpuPlanDigest(explicit.GpuOwnerAttachment.GpuPlan)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = s.AcceptCreate(ctx, explicit); err != nil || len(repo.creates) != 2 || repo.creates[1].Spec.GPU.RuntimeClass != "provider-runtime" {
				t.Fatal("owner fallback replaced explicit provider runtime", err)
			}
		})
	}
}
