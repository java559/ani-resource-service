package server

import (
	"context"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/compute/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/compute"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

var computeGovernanceMethods = map[string]struct{}{
	biz.ListMethod: {}, biz.ResolveMethod: {},
}
var computePlatformMethods = map[string]struct{}{
	"/compute.v1.ComputePresetService/AdminListPresets":      {},
	"/compute.v1.ComputePresetService/AdminGetPreset":        {},
	"/compute.v1.ComputePresetService/AdminCreatePreset":     {},
	"/compute.v1.ComputePresetService/AdminUpdatePreset":     {},
	"/compute.v1.ComputePresetService/AdminSetPresetEnabled": {},
	"/compute.v1.ComputePresetService/AdminDeletePreset":     {},
}

func computeScope(ctx context.Context, p governancePrincipal, method string, usage biz.Usage) context.Context {
	return biz.WithScope(ctx, biz.Scope{TenantID: p.TenantID, Actor: p.Actor, RequestID: p.RequestID, Workload: p.Workload, Method: method, Platform: p.Platform, Usage: usage})
}
func withComputeGovernanceScope(ctx context.Context, p governancePrincipal, r proto.Message, method string) (context.Context, error) {
	if p.Platform {
		return computeScope(ctx, p, method, biz.UsageUnspecified), nil
	}
	var tenant string
	var usage biz.Usage
	switch v := r.(type) {
	case *pb.ListPresetsRequest:
		if method != biz.ListMethod {
			return nil, status.Error(codes.PermissionDenied, "compute RPC differs from authenticated scope")
		}
		tenant = v.TenantId
		usage = biz.Usage(v.Usage)
	case *pb.ResolveComputePresetRequest:
		if method != biz.ResolveMethod {
			return nil, status.Error(codes.PermissionDenied, "compute RPC differs from authenticated scope")
		}
		tenant = v.TenantId
		usage = biz.Usage(v.Usage)
	default:
		return nil, status.Error(codes.PermissionDenied, "compute request differs from authenticated method")
	}
	if tenant == "" || tenant != p.TenantID {
		return nil, status.Error(codes.PermissionDenied, "compute tenant differs from authenticated scope")
	}
	if !biz.ValidUsage(usage) {
		return nil, status.Error(codes.InvalidArgument, "INVALID_COMPUTE_USAGE")
	}
	return computeScope(ctx, p, method, usage), nil
}
