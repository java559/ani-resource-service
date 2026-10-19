package instance

import (
	"context"

	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// ResolveInstanceMetricScope reads the original persisted location only. A
// stopped or deleted runtime does not revoke access to retained metric history.
func (s *Service) ResolveInstanceMetricScope(ctx context.Context, r *pb.GetInstanceRequest) (*pb.InstanceMetricScope, error) {
	if r == nil || !biz.UUID(r.TenantId) || !biz.UUID(r.InstanceId) {
		return nil, rpcError(biz.ErrInvalid)
	}
	if err := biz.RequireTenant(ctx, r.TenantId); err != nil {
		return nil, rpcError(err)
	}
	i, err := s.repo.Get(ctx, r.TenantId, r.InstanceId)
	if err != nil {
		return nil, rpcError(err)
	}
	if i.TenantID != r.TenantId || i.ID != r.InstanceId {
		return nil, rpcError(biz.ErrDenied)
	}
	// ResolveNamespace normally supplies a create-time default. An absent
	// persisted namespace must never take that fallback on historical reads.
	_, namespaceErr := biz.ResolveNamespace(i.TenantID, i.Spec.Namespace, "")
	if !biz.UUID(i.Spec.ClusterID) || i.Spec.Namespace == "" || namespaceErr != nil {
		const reason = "INSTANCE_METRIC_SCOPE_UNAVAILABLE"
		detailed, err := status.New(codes.FailedPrecondition, reason).WithDetails(&errdetails.ErrorInfo{Domain: "instance.ani.io", Reason: reason})
		if err != nil {
			return nil, status.Error(codes.Internal, "failed to encode instance metric scope error")
		}
		return nil, detailed.Err()
	}
	return &pb.InstanceMetricScope{TenantId: i.TenantID, InstanceId: i.ID, ClusterId: i.Spec.ClusterID, Namespace: i.Spec.Namespace, ContainerName: biz.Container}, nil
}
