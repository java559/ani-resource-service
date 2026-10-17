package instancev1

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
)

// CanonicalBusinessPayload is shared by Gov and Res. Trusted attachments and
// command identities are separate messages and never enter the public hash.
func CanonicalBusinessPayload(m proto.Message) ([]byte, error) {
	switch m.(type) {
	case *CreateInstanceData, *CreateBusiness:
	default:
		return nil, errors.New("unsupported instance business message")
	}
	if m == nil || !m.ProtoReflect().IsValid() || hasUnknown(m.ProtoReflect()) {
		return nil, errors.New("invalid instance business")
	}
	raw, e := (protojson.MarshalOptions{UseProtoNames: true, EmitUnpopulated: true}).Marshal(m)
	if e != nil {
		return nil, e
	}
	var value any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if e = dec.Decode(&value); e != nil {
		return nil, e
	}
	var out bytes.Buffer
	enc := json.NewEncoder(&out)
	enc.SetEscapeHTML(false)
	if e = enc.Encode(value); e != nil {
		return nil, e
	}
	return bytes.TrimSuffix(out.Bytes(), []byte{'\n'}), nil
}
func BusinessPayloadDigest(m proto.Message) (string, error) {
	raw, e := CanonicalBusinessPayload(m)
	if e != nil {
		return "", e
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}
func DecodeBusinessPayload(raw []byte) (*CreateBusiness, error) {
	b := new(CreateBusiness)
	if e := protojson.Unmarshal(raw, b); e != nil {
		return nil, e
	}
	if e := ValidateCreateBusiness(b); e != nil {
		return nil, e
	}
	return b, nil
}

var ErrInvalidBusiness = errors.New("INVALID_INSTANCE_REQUEST")
var ErrInvalidScheduling = errors.New("INVALID_SCHEDULING_OPTIONS")
var ErrSchedulingConflict = errors.New("SCHEDULING_FIELD_CONFLICT")

var runtimeClassLabel = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)

// ValidateGPURuntimeClassName checks the managed owner RuntimeClass name.
func ValidateGPURuntimeClassName(name string) error {
	if len(name) == 0 || len(name) > 253 {
		return ErrInvalidBusiness
	}
	for _, label := range strings.Split(name, ".") {
		if len(label) > 63 || !runtimeClassLabel.MatchString(label) {
			return ErrInvalidBusiness
		}
	}
	return nil
}

// ValidateSchedulingOptions preserves optional presence. Effective options always
// contain a concrete placement; raw intent may omit the whole object.
func ValidateSchedulingOptions(s *SchedulingOptions, effective bool) error {
	if s == nil {
		if effective {
			return ErrInvalidScheduling
		}
		return nil
	}
	if len(s.ProtoReflect().GetUnknown()) != 0 || (s.PodPlacement == nil && s.PriorityClassName == nil) {
		return ErrInvalidScheduling
	}
	if effective && s.PodPlacement == nil {
		return ErrInvalidScheduling
	}
	if s.PodPlacement != nil && (*s.PodPlacement < SchedulingOptions_NONE || *s.PodPlacement > SchedulingOptions_SPREAD_PREFERRED) {
		return ErrInvalidScheduling
	}
	if s.PriorityClassName != nil && (strings.TrimSpace(*s.PriorityClassName) != *s.PriorityClassName || *s.PriorityClassName == "") {
		return ErrInvalidScheduling
	}
	return nil
}
func ResolveSchedulingOptions(raw, defaults *SchedulingOptions) (*SchedulingOptions, error) {
	if err := ValidateSchedulingOptions(raw, false); err != nil {
		return nil, err
	}
	if defaults != nil {
		if err := ValidateSchedulingOptions(defaults, false); err != nil {
			return nil, err
		}
	}
	out := &SchedulingOptions{PodPlacement: SchedulingOptions_NONE.Enum()}
	for _, s := range []*SchedulingOptions{defaults, raw} {
		if s == nil {
			continue
		}
		if s.PodPlacement != nil {
			out.PodPlacement = s.GetPodPlacement().Enum()
		}
		if s.PriorityClassName != nil {
			v := s.GetPriorityClassName()
			out.PriorityClassName = &v
		}
	}
	return out, nil
}
func ValidateSchedulingConsistency(raw, effective *SchedulingOptions) error {
	if err := ValidateSchedulingOptions(effective, true); err != nil {
		return err
	}
	if err := ValidateSchedulingOptions(raw, false); err != nil {
		return err
	}
	if raw == nil {
		return nil
	}
	if raw.PodPlacement != nil && raw.GetPodPlacement() != effective.GetPodPlacement() {
		return fmt.Errorf("%w: pod_placement", ErrSchedulingConflict)
	}
	if raw.PriorityClassName != nil && (effective.PriorityClassName == nil || raw.GetPriorityClassName() != effective.GetPriorityClassName()) {
		return fmt.Errorf("%w: priority_class_name", ErrSchedulingConflict)
	}
	return nil
}
func ValidateCreateBusiness(b *CreateBusiness) error {
	if b == nil || b.Data == nil || b.Spec == nil || hasUnknown(b.ProtoReflect()) {
		return ErrInvalidBusiness
	}
	f := b.Spec
	if f.InstanceKind != "container" || (f.ComputeType != "cpu" && f.ComputeType != "gpu") {
		return ErrInvalidBusiness
	}
	if err := ValidateSchedulingConsistency(b.Data.Scheduling, f.Scheduling); err != nil {
		return err
	}
	if f.GpuRuntimeClassName != nil && ValidateGPURuntimeClassName(f.GetGpuRuntimeClassName()) != nil {
		return ErrInvalidBusiness
	}
	if f.ComputeType == "cpu" && (b.Gpu != nil || f.GpuSummary != nil || f.GpuSpecDigest != nil || f.GpuRuntimeClassName != nil) {
		return ErrInvalidBusiness
	}
	if f.ComputeType == "gpu" && (f.GpuSpecDigest == nil || len(f.GetGpuSpecDigest()) != 64 || b.Gpu == nil || f.GpuSummary == nil || b.Gpu.ProfileId != f.GpuSummary.ProfileId || b.Gpu.ProfileVersion != f.GpuSummary.ProfileVersion || b.Gpu.DevicesPerReplica != f.GpuSummary.DevicesPerInstance) {
		return ErrInvalidBusiness
	}
	return nil
}

// Reserved fields must not disappear when gRPC messages enter the JSON digest.
func hasUnknown(m protoreflect.Message) bool {
	if len(m.GetUnknown()) != 0 {
		return true
	}
	unknown := false
	m.Range(func(field protoreflect.FieldDescriptor, value protoreflect.Value) bool {
		if field.IsMap() {
			if field.MapValue().Kind() == protoreflect.MessageKind {
				value.Map().Range(func(_ protoreflect.MapKey, v protoreflect.Value) bool {
					unknown = hasUnknown(v.Message())
					return !unknown
				})
			}
		} else if field.IsList() {
			if field.Kind() == protoreflect.MessageKind {
				list := value.List()
				for k := 0; k < list.Len(); k++ {
					if hasUnknown(list.Get(k).Message()) {
						unknown = true
						break
					}
				}
			}
		} else if field.Kind() == protoreflect.MessageKind {
			unknown = hasUnknown(value.Message())
		}
		return !unknown
	})
	return unknown
}
