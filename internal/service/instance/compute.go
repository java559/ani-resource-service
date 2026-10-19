package instance

import (
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	compute "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/compute"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"strconv"
)

func wireReference(g *compute.GPUReference) *pb.GpuPresetReference {
	if g == nil {
		return nil
	}
	return &pb.GpuPresetReference{ProfileId: g.ProfileID, ProfileVersion: g.ProfileVersion, SpecDigest: g.SpecDigest, DevicesPerInstance: g.DevicesPerInstance, ClusterId: g.ClusterID, PoolId: g.PoolID, Mode: acc.SupplyMode(acc.SupplyMode_value[g.Mode]), ModelKey: g.ModelKey, MemoryMibPerDevice: g.MemoryMiBPerDevice, CoreLimitPercent: g.CoreLimitPercent}
}
func wireSummary(g *biz.GPUSummary, execution biz.GPU) *pb.GpuSummary {
	if g == nil {
		return nil
	}
	n, _ := strconv.ParseUint(execution.Limits["volcano.sh/vgpu-number"], 10, 32)
	m, _ := strconv.ParseInt(execution.Limits["volcano.sh/vgpu-memory"], 10, 64)
	c, _ := strconv.ParseUint(execution.Limits["volcano.sh/vgpu-cores"], 10, 32)
	return &pb.GpuSummary{ProfileId: g.ProfileID, ProfileVersion: g.ProfileVersion, Mode: acc.SupplyMode(acc.SupplyMode_value[g.Mode]), ModelKey: g.ModelKey, DevicesPerInstance: uint32(n), MemoryMibPerDevice: m, MemoryMibTotal: m * int64(n), CoreLimitPercent: uint32(c)}
}
func domainScheduling(s *pb.SchedulingOptions) biz.Scheduling {
	return biz.Scheduling{PodPlacement: s.GetPodPlacement().String(), PriorityClassName: s.PriorityClassName}
}
func wireScheduling(s biz.Scheduling) *pb.SchedulingOptions {
	return &pb.SchedulingOptions{PodPlacement: pb.SchedulingOptions_PodPlacement(pb.SchedulingOptions_PodPlacement_value[s.PodPlacement]).Enum(), PriorityClassName: s.PriorityClassName}
}
