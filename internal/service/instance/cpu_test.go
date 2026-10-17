package instance

import (
	"context"
	"strings"
	"testing"

	attachment "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/integration/v1"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

// These tests stop at the outbound repository boundary. Durable admission and
// fixed-type conflict handling are exercised by the real PostgreSQL tests.
type cpuIntake struct {
	biz.Repository
	biz.PresetRepository
	preset  biz.Preset
	creates []biz.Create
	deletes []biz.Delete
}

func (r *cpuIntake) AcceptCreate(_ context.Context, c biz.Create) (biz.ACK, error) {
	if err := biz.ValidateCreate(c); err != nil {
		return biz.ACK{}, err
	}
	r.creates = append(r.creates, c)
	return biz.ACK{OperationID: c.OperationID, ResourceID: c.ID, Accepted: true}, nil
}

func (r *cpuIntake) AcceptDelete(_ context.Context, d biz.Delete) (biz.ACK, error) {
	if err := biz.ValidateDelete(d); err != nil {
		return biz.ACK{}, err
	}
	r.deletes = append(r.deletes, d)
	return biz.ACK{OperationID: d.OperationID, ResourceID: d.ID, Accepted: true}, nil
}

type cpuImageBoundary struct{ calls int }

func (r *cpuImageBoundary) Resolve(context.Context, string, string, string) (biz.ResolvedImage, error) {
	r.calls++
	digest := "sha256:" + strings.Repeat("a", 64)
	return biz.ResolvedImage{Reference: "registry.invalid/work@" + digest, Digest: digest}, nil
}

type cpuEnvironmentBoundary struct{ calls int }

func (r *cpuEnvironmentBoundary) ValidateEnvironment(context.Context, string, string, string, string) error {
	r.calls++
	return nil
}

func TestCPUServiceFrozenResolutionAndIntake(t *testing.T) {
	const tenant = "11111111-1111-4111-8111-111111111111"
	const resource = "22222222-2222-4222-8222-222222222222"
	const operation = "33333333-3333-4333-8333-333333333333"
	const actor = "governance:user:42"
	repo, images, environment := &cpuIntake{}, &cpuImageBoundary{}, &cpuEnvironmentBoundary{}
	repo.preset = biz.Preset{ID: resource, Code: "cpu-small", Name: "CPU", ComputeType: biz.TypeCPU, CPUMillis: 1000, MemoryMiB: 2048, Enabled: true}
	s, err := NewService(repo, images, environment, Config{NamespacePrefix: "tenant-", ClusterID: tenant, ServiceAccount: "workload", StorageClass: "ani-block", WorkspaceSize: "1Gi", Shell: "/bin/bash", UID: 1000, GID: 1000, GPURuntimeClassName: "nvidia"})
	if err != nil {
		t.Fatal(err)
	}
	ctx := biz.WithCaller(context.Background(), biz.Caller{TenantID: tenant, Actor: actor})
	presets, err := s.ListPresets(ctx, &pb.ListPresetsRequest{TenantId: tenant})
	if err != nil || len(presets.Items) != 1 || presets.Items[0].ComputeType != biz.TypeCPU || presets.Items[0].Gpu != nil {
		t.Fatal("CPU preset not exposed without GPU dependencies", presets, err)
	}
	business, err := s.ResolveCreateSpec(ctx, &pb.ResolveCreateSpecRequest{TenantId: tenant, Data: &pb.CreateInstanceData{Name: "cpu-work", ImageId: "img_" + strings.Repeat("a", 32), ImageScope: "platform", PresetId: resource}})
	if err != nil {
		t.Fatal(err)
	}
	if business.Gpu != nil || business.Spec.GpuRuntimeClassName != nil || business.Spec.ComputeType != biz.TypeCPU || business.Spec.Cpu != "1000m" || business.Spec.Memory != "2048Mi" || business.Spec.Namespace != "tenant-"+tenant || images.calls != 1 || environment.calls != 1 {
		t.Fatal("CPU resolution did not freeze the configured runtime and external image", business)
	}
	digest, err := pb.BusinessPayloadDigest(business)
	if err != nil {
		t.Fatal(err)
	}
	request := &pb.AcceptCreateRequest{TenantId: tenant, OperationId: operation, Business: business, CpuOwnerAttachment: &pb.CpuOwnerCreateAttachment{ResourceId: resource, RequestHash: strings.Repeat("b", 64), BusinessPayloadDigest: digest, Actor: actor}}
	accepted, err := s.AcceptCreate(ctx, request)
	if err != nil || accepted.GetDurableOwnerAck().GetResourceId() != resource || !accepted.GetDurableOwnerAck().GetAccepted() || len(repo.creates) != 1 {
		t.Fatal("trusted CPU create did not reach the repository", accepted, err)
	}
	c := repo.creates[0]
	if c.TenantID != tenant || c.ID != resource || c.OperationID != operation || c.Spec.ComputeType != biz.TypeCPU || !c.Spec.GPU.Empty() || len(c.Charges) != 0 || c.Spec.NotebookName != "instance-"+resource || c.Spec.PVCName != "workspace-"+resource {
		t.Fatal("CPU intake gained GPU state or lost its trusted identity", c)
	}
	for name, mutate := range map[string]func(*pb.AcceptCreateRequest){
		"mixed attachments": func(r *pb.AcceptCreateRequest) { r.GpuOwnerAttachment = &attachment.GpuOwnerCreateAttachment{} },
		"GPU charge": func(r *pb.AcceptCreateRequest) {
			r.OriginalCharges = []*pb.OriginalQuotaCharge{{ChargeId: resource, QuotaCode: "gpu.shared_memory_mib", OriginalUnits: 1}}
		},
		"GPU request in CPU business":                 func(r *pb.AcceptCreateRequest) { r.Business.Gpu = &acc.GpuRequest{} },
		"GPU type through CPU attachment":             func(r *pb.AcceptCreateRequest) { r.Business.Spec.ComputeType = biz.TypeGPU },
		"missing compute type through CPU attachment": func(r *pb.AcceptCreateRequest) { r.Business.Spec.ComputeType = "" },
		"CPU type through GPU attachment": func(r *pb.AcceptCreateRequest) {
			r.CpuOwnerAttachment = nil
			r.GpuOwnerAttachment = &attachment.GpuOwnerCreateAttachment{}
		},
		"changed frozen business": func(r *pb.AcceptCreateRequest) { r.Business.Spec.Cpu = "2" },
	} {
		t.Run(name, func(t *testing.T) {
			changed := proto.Clone(request).(*pb.AcceptCreateRequest)
			mutate(changed)
			if _, err := s.AcceptCreate(ctx, changed); status.Code(err) != codes.InvalidArgument {
				t.Fatal("invalid CPU/GPU transport combination reached intake", err)
			}
			if len(repo.creates) != 1 {
				t.Fatal("rejected create reached persistence")
			}
		})
	}
	wrongActor := proto.Clone(request).(*pb.AcceptCreateRequest)
	wrongActor.CpuOwnerAttachment.Actor = "governance:user:other"
	if _, err := s.AcceptCreate(ctx, wrongActor); status.Code(err) != codes.PermissionDenied {
		t.Fatal("CPU attachment actor was not bound to the trusted caller", err)
	}
	foreign := biz.WithCaller(context.Background(), biz.Caller{TenantID: resource, Actor: actor})
	for name, caller := range map[string]context.Context{"missing caller": context.Background(), "foreign tenant": foreign} {
		t.Run(name, func(t *testing.T) {
			if _, err := s.AcceptCreate(caller, request); status.Code(err) != codes.PermissionDenied {
				t.Fatal("CPU admission ignored trusted tenant scope", err)
			}
			if _, err := s.ResolveCreateSpec(caller, &pb.ResolveCreateSpecRequest{TenantId: tenant, Data: business.Data}); status.Code(err) != codes.PermissionDenied {
				t.Fatal("CPU resolution ignored trusted tenant scope", err)
			}
		})
	}
	deleteRequest := &pb.AcceptDeleteRequest{TenantId: tenant, OperationId: resource, ExpectedVersion: 1, CpuOwnerAttachment: &pb.CpuOwnerDeleteAttachment{ResourceId: resource, CreateOperationId: operation, RequestHash: strings.Repeat("c", 64), Actor: actor}}
	if _, err := s.AcceptDelete(ctx, deleteRequest); err != nil || len(repo.deletes) != 1 {
		t.Fatal("trusted CPU delete required a GPU attachment", err)
	}
	d := repo.deletes[0]
	if d.ComputeType != biz.TypeCPU || !d.GPU.Empty() || len(d.Charges) != 0 || d.TenantID != tenant || d.CreateOperationID != operation || d.ExpectedVersion != 1 {
		t.Fatal("CPU deletion lost its original identity or gained GPU state", d)
	}
	mixedDelete := proto.Clone(deleteRequest).(*pb.AcceptDeleteRequest)
	mixedDelete.GpuOwnerAttachment = &attachment.GpuOwnerDeleteAttachment{}
	if _, err := s.AcceptDelete(ctx, mixedDelete); status.Code(err) != codes.InvalidArgument {
		t.Fatal("mixed CPU/GPU delete accepted", err)
	}
	wrongDeleteActor := proto.Clone(deleteRequest).(*pb.AcceptDeleteRequest)
	wrongDeleteActor.CpuOwnerAttachment.Actor = "governance:user:other"
	if _, err := s.AcceptDelete(ctx, wrongDeleteActor); status.Code(err) != codes.PermissionDenied {
		t.Fatal("CPU delete ignored trusted actor", err)
	}
	if _, err := s.AcceptDelete(foreign, deleteRequest); status.Code(err) != codes.PermissionDenied {
		t.Fatal("CPU delete ignored trusted tenant", err)
	}
	if len(repo.creates) != 1 || len(repo.deletes) != 1 {
		t.Fatal("rejected transport requests reached persistence")
	}
}

func (r *cpuIntake) GetPreset(_ context.Context, id string) (biz.Preset, error) {
	if id != r.preset.ID {
		return biz.Preset{}, biz.ErrNotFound
	}
	return r.preset, nil
}
func (r *cpuIntake) ListPresets(context.Context, biz.PresetFilter) ([]biz.Preset, int64, bool, error) {
	return []biz.Preset{r.preset}, 1, false, nil
}
