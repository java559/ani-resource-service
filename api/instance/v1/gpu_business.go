package instancev1

import (
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	"google.golang.org/protobuf/proto"
)

// ValidateGpuBusinessPlan binds the owner's frozen managed reference and safe
// summary to the complete ACC plan. It does not replace plan/charge validation.
func ValidateGpuBusinessPlan(b *CreateBusiness, p *acc.ResolvedGpuPlan) error {
	if e := ValidateCreateBusiness(b); e != nil {
		return e
	}
	if b.Spec.ComputeType == "cpu" {
		if p != nil {
			return ErrInvalidBusiness
		}
		return nil
	}
	if p == nil || p.Request == nil || p.Profile == nil || p.Profile.Spec == nil || p.Totals == nil || !proto.Equal(p.Request, b.Gpu) {
		return ErrInvalidBusiness
	}
	f, g, s, r := b.Spec, b.Spec.GpuSummary, p.Profile.Spec, p.Request
	if r.Replicas != 1 || r.ContainerName != "workspace" || r.ClusterId != f.ClusterId || p.Profile.SpecDigest != f.GetGpuSpecDigest() || p.Profile.ProfileId != r.ProfileId || p.Profile.ProfileVersion != r.ProfileVersion {
		return ErrInvalidBusiness
	}
	if g == nil || g.ProfileId != r.ProfileId || g.ProfileVersion != r.ProfileVersion || g.Mode != s.Mode || g.ModelKey != s.ModelKey || g.DevicesPerInstance != r.DevicesPerReplica || g.CoreLimitPercent != s.CoreLimitPercent {
		return ErrInvalidBusiness
	}
	switch s.Mode {
	case acc.SupplyMode_SHARED_FIXED:
		if g.SharedMemoryMibPerDevice == nil || g.SharedMemoryMibTotal == nil || *g.SharedMemoryMibPerDevice != s.SharedMemoryMib || *g.SharedMemoryMibTotal != p.Totals.SharedMemoryMib {
			return ErrInvalidBusiness
		}
	case acc.SupplyMode_WHOLE_EXCLUSIVE:
		if g.SharedMemoryMibPerDevice != nil || g.SharedMemoryMibTotal != nil {
			return ErrInvalidBusiness
		}
	default:
		return ErrInvalidBusiness
	}
	return nil
}
