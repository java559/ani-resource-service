package vm

import (
	"context"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/vm/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

func (s *Service) CreateDisk(ctx context.Context, r *pb.CreateDiskRequest) (*pb.Operation, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	return s.command(ctx, r, "create-disk")
}
func (s *Service) AttachDisk(ctx context.Context, r *pb.DiskChangeRequest) (*pb.Operation, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	return s.command(ctx, r, "attach-disk")
}
func (s *Service) DetachDisk(ctx context.Context, r *pb.DiskChangeRequest) (*pb.Operation, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	return s.command(ctx, r, "detach-disk")
}
func (s *Service) DeleteDisk(ctx context.Context, r *pb.DiskChangeRequest) (*pb.Operation, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	return s.command(ctx, r, "delete-disk")
}
