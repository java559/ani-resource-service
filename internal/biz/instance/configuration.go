package instance

import (
	"errors"
	"github.com/zhangzhe-ctrl/ani-resource-service/pkg/instanceconfig"
)

// ValidateConfiguration also guards decoded snapshots. Version 1 never renders
// fields which were absent from its immutable command digest.
func ValidateConfiguration(s Spec) error {
	if s.ContractVersion <= 1 {
		if s.WorkspaceMountPath != "" || len(s.VolumeMounts) != 0 || s.Network != nil || len(s.Labels) != 0 || len(s.Annotations) != 0 || len(s.Env) != 0 {
			return ErrInvalid
		}
		return nil
	}
	if s.ContractVersion != 2 {
		return ErrInvalid
	}
	paths := make([]string, 0, len(s.VolumeMounts))
	for _, m := range s.VolumeMounts {
		if m.ClusterID != s.ClusterID || m.Namespace != s.Namespace || m.PVCUID == "" || m.PVCName == "" || m.Ownership != "borrowed" {
			return ErrInvalid
		}
		paths = append(paths, m.MountPath)
	}
	names, values := make([]string, 0, len(s.Env)), make([]string, 0, len(s.Env))
	for _, v := range s.Env {
		names = append(names, v.Name)
		values = append(values, v.Value)
	}
	if s.Network != nil && (s.Network.VPCID == "" || s.Network.SubnetID == "") {
		return ErrInvalid
	}
	err := instanceconfig.Validate(s.WorkspaceMountPath, paths, s.Labels, s.Annotations, names, values)
	if errors.Is(err, instanceconfig.ErrReserved) {
		return ErrReservedConfig
	}
	if err != nil {
		return ErrInvalid
	}
	return nil
}
