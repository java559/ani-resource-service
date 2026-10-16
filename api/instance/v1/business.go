package instancev1

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"

	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// CanonicalBusinessPayload is shared by Gov and Res. Trusted attachments and
// command identities are separate messages and never enter the public hash.
func CanonicalBusinessPayload(m proto.Message) ([]byte, error) {
	switch m.(type) {
	case *CreateInstanceData, *CreateBusiness:
	default:
		return nil, errors.New("unsupported instance business message")
	}
	if m == nil || !m.ProtoReflect().IsValid() || len(m.ProtoReflect().GetUnknown()) != 0 {
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
	// Preserve canonical bytes for accepted GPU snapshots from before this field.
	if b, ok := m.(*CreateBusiness); ok && b.Spec != nil && b.Spec.InstanceType == "" {
		if object, ok := value.(map[string]any); ok {
			if spec, ok := object["spec"].(map[string]any); ok {
				delete(spec, "instance_type")
			}
		}
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
	return b, nil
}
