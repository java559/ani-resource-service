package instance

import (
	"context"

	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
)

func (s *Service) acceptCPUCreate(ctx context.Context, r *pb.AcceptCreateRequest) (*pb.OperationResponse, error) {
	a, b := r.CpuOwnerAttachment, r.Business
	caller, err := biz.CallerFromContext(ctx)
	if err != nil || a.Actor != caller.Actor {
		return nil, rpcError(biz.ErrDenied)
	}
	if r.GpuOwnerAttachment != nil || len(r.OriginalCharges) != 0 || b.Gpu != nil || b.Spec.ComputeType != biz.TypeCPU {
		return nil, rpcError(biz.ErrInvalid)
	}
	digest, err := pb.BusinessPayloadDigest(b)
	if err != nil || digest != a.BusinessPayloadDigest {
		return nil, rpcError(biz.ErrInvalid)
	}
	d, f := b.Data, b.Spec
	spec := biz.Spec{InstanceKind: biz.KindContainer, ComputeType: biz.TypeCPU, PresetName: f.PresetName, PresetCode: f.PresetCode, Scheduling: domainScheduling(f.Scheduling), Name: d.Name, ImageID: d.ImageId, ImageScope: d.ImageScope, ImageReference: f.ImageReference, ImageDigest: f.ImageDigest, PresetID: d.PresetId, CPU: f.Cpu, Memory: f.Memory, UID: f.Uid, GID: f.Gid, Shell: f.Shell, ServiceAccount: f.ServiceAccount, StorageClass: f.StorageClass, WorkspaceSize: f.WorkspaceSize, ClusterID: f.ClusterId, Namespace: f.Namespace, NotebookName: "instance-" + a.ResourceId, PVCName: "workspace-" + a.ResourceId, ImagePullSecret: "ani-instance-image-pull"}
	accepted, err := s.repo.AcceptCreate(ctx, biz.Create{TenantID: r.TenantId, ID: a.ResourceId, OperationID: r.OperationId, RequestHash: a.RequestHash, BusinessDigest: digest, Spec: spec})
	return ack(accepted), rpcError(err)
}

func (s *Service) acceptCPUDelete(ctx context.Context, r *pb.AcceptDeleteRequest) (*pb.OperationResponse, error) {
	a := r.CpuOwnerAttachment
	caller, err := biz.CallerFromContext(ctx)
	if err != nil || a.Actor != caller.Actor {
		return nil, rpcError(biz.ErrDenied)
	}
	if r.GpuOwnerAttachment != nil || len(r.OriginalCharges) != 0 {
		return nil, rpcError(biz.ErrInvalid)
	}
	accepted, err := s.repo.AcceptDelete(ctx, biz.Delete{InstanceKind: biz.KindContainer, ComputeType: biz.TypeCPU, TenantID: r.TenantId, ID: a.ResourceId, OperationID: r.OperationId, CreateOperationID: a.CreateOperationId, RequestHash: a.RequestHash, ExpectedVersion: r.ExpectedVersion})
	return ack(accepted), rpcError(err)
}
