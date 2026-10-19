package computev1

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	accelerator "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
)

const SelectionSchemaVersion uint32 = 1

var ErrInvalidSelection = errors.New("INVALID_COMPUTE_SELECTION")
var selectionCode = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)

func selectionUUID(s string) bool {
	id, e := uuid.Parse(s)
	return e == nil && id != uuid.Nil && id.String() == s
}
func selectionDigestValue(s string) bool {
	if len(s) != 64 || strings.ToLower(s) != s {
		return false
	}
	_, e := hex.DecodeString(s)
	return e == nil
}

// Field declaration order is the versioned canonical JSON order. CPU encodes
// gpu=null; usage, enabled, audit data and the digest itself are excluded.
type canonicalGPU struct {
	ProfileID          string `json:"profile_id"`
	ProfileVersion     uint64 `json:"profile_version"`
	SpecDigest         string `json:"spec_digest"`
	DevicesPerUnit     uint32 `json:"devices_per_unit"`
	ClusterID          string `json:"cluster_id"`
	PoolID             string `json:"pool_id"`
	Mode               string `json:"mode"`
	ModelKey           string `json:"model_key"`
	MemoryMiBPerDevice int64  `json:"memory_mib_per_device"`
	CoreLimitPercent   uint32 `json:"core_limit_percent"`
}
type canonicalSelection struct {
	SchemaVersion uint32        `json:"schema_version"`
	PresetID      string        `json:"preset_id"`
	Code          string        `json:"code"`
	Name          string        `json:"name"`
	ComputeType   string        `json:"compute_type"`
	CPUMillis     int64         `json:"cpu_millis"`
	MemoryMiB     int64         `json:"memory_mib"`
	GPU           *canonicalGPU `json:"gpu"`
}

func CanonicalSelection(s *ComputeSelection) ([]byte, error) {
	if s == nil || len(s.ProtoReflect().GetUnknown()) != 0 || s.SchemaVersion != SelectionSchemaVersion || !selectionUUID(s.PresetId) || !selectionCode.MatchString(s.Code) || strings.TrimSpace(s.Name) == "" || utf8.RuneCountInString(s.Name) > 128 || s.CpuMillis <= 0 || s.MemoryMib <= 0 || s.MemoryMib > math.MaxInt64/(1024*1024) {
		return nil, ErrInvalidSelection
	}
	c := canonicalSelection{SchemaVersion: s.SchemaVersion, PresetID: s.PresetId, Code: s.Code, Name: s.Name, ComputeType: s.ComputeType, CPUMillis: s.CpuMillis, MemoryMiB: s.MemoryMib}
	switch s.ComputeType {
	case "cpu":
		if s.Gpu != nil {
			return nil, ErrInvalidSelection
		}
	case "gpu":
		g := s.Gpu
		if g == nil || len(g.ProtoReflect().GetUnknown()) != 0 || !selectionUUID(g.ProfileId) || g.ProfileVersion == 0 || !selectionDigestValue(g.SpecDigest) || !selectionUUID(g.ClusterId) || !selectionUUID(g.PoolId) || strings.TrimSpace(g.ModelKey) == "" || g.MemoryMibPerDevice <= 0 {
			return nil, ErrInvalidSelection
		}
		switch g.Mode {
		case accelerator.SupplyMode_WHOLE_EXCLUSIVE:
			if g.DevicesPerUnit == 0 || g.CoreLimitPercent != 100 {
				return nil, ErrInvalidSelection
			}
		case accelerator.SupplyMode_SHARED_FIXED:
			if g.DevicesPerUnit != 1 || g.CoreLimitPercent < 1 || g.CoreLimitPercent > 99 {
				return nil, ErrInvalidSelection
			}
		default:
			return nil, ErrInvalidSelection
		}
		c.GPU = &canonicalGPU{ProfileID: g.ProfileId, ProfileVersion: g.ProfileVersion, SpecDigest: g.SpecDigest, DevicesPerUnit: g.DevicesPerUnit, ClusterID: g.ClusterId, PoolID: g.PoolId, Mode: g.Mode.String(), ModelKey: g.ModelKey, MemoryMiBPerDevice: g.MemoryMibPerDevice, CoreLimitPercent: g.CoreLimitPercent}
	default:
		return nil, ErrInvalidSelection
	}
	return json.Marshal(c)
}
func SelectionDigest(s *ComputeSelection) (string, error) {
	b, e := CanonicalSelection(s)
	if e != nil {
		return "", e
	}
	sum := sha256.Sum256(append([]byte("compute-selection-v1\n"), b...))
	return hex.EncodeToString(sum[:]), nil
}
func ValidateSelection(s *ComputeSelection) error {
	d, e := SelectionDigest(s)
	if e != nil {
		return e
	}
	if !selectionDigestValue(s.SelectionDigest) || s.SelectionDigest != d {
		return ErrInvalidSelection
	}
	return nil
}
