package service

import (
	"context"
	networkv1 "github.com/zhangzhe-ctrl/ani-resource-service/api/network/v1"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type PlatformDeviceRetirementService struct {
	networkv1.UnimplementedPlatformDeviceRetirementServiceServer
	egress *biz.Egress
}

func NewPlatformDeviceRetirementService(e *biz.Egress) *PlatformDeviceRetirementService {
	return &PlatformDeviceRetirementService{egress: e}
}
func wireDeviceRetirement(v biz.DeviceRetirement) *networkv1.DeviceRetirement {
	return &networkv1.DeviceRetirement{DeviceId: v.DeviceID, OperationId: v.OperationID, State: v.State, Reason: v.Reason, Version: v.Version, ExpectedVersion: v.ExpectedVersion, ConfigUid: v.ConfigUID, FenceVersion: v.FenceVersion, PendingUnknown: v.PendingUnknown, CreatedAt: timestamppb.New(v.CreatedAt), UpdatedAt: timestamppb.New(v.UpdatedAt), CompletedAt: optionalTime(v.CompletedAt)}
}
func (s *PlatformDeviceRetirementService) RetireNetworkDevice(ctx context.Context, r *networkv1.RetireNetworkDeviceRequest) (*networkv1.RetireNetworkDeviceResponse, error) {
	v, err := s.egress.RetireNetworkDevice(ctx, biz.DeviceRetirementIntent{DeviceID: r.GetDeviceId(), ExpectedVersion: r.GetExpectedVersion(), IdempotencyKey: r.GetIdempotencyKey()})
	if err != nil {
		return nil, rpcError(err)
	}
	return &networkv1.RetireNetworkDeviceResponse{Retirement: wireDeviceRetirement(v)}, nil
}
func (s *PlatformDeviceRetirementService) GetNetworkDeviceRetirement(ctx context.Context, r *networkv1.GetNetworkDeviceRetirementRequest) (*networkv1.GetNetworkDeviceRetirementResponse, error) {
	v, err := s.egress.GetNetworkDeviceRetirement(ctx, r.GetDeviceId())
	if err != nil {
		return nil, rpcError(err)
	}
	return &networkv1.GetNetworkDeviceRetirementResponse{Retirement: wireDeviceRetirement(v)}, nil
}
