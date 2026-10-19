package instancev1

import (
	"bytes"
	"encoding/json"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"testing"
)

func TestV1CanonicalProjectionDoesNotFollowDescriptor(t *testing.T) {
	raw, err := CanonicalBusinessPayload(&CreateInstanceData{Name: "example"})
	if err != nil {
		t.Fatal(err)
	}
	const legacy = `{"image_id":"","image_scope":"","name":"example","preset_id":"","scheduling":null}`
	if string(raw) != legacy {
		t.Fatalf("legacy request bytes changed: %s", raw)
	}
	spec := currentBusiness()
	raw, err = CanonicalBusinessPayload(spec)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("instance_contract_version")) || bytes.Contains(raw, []byte("volume_mounts")) {
		t.Fatal("v1 frozen bytes include v2 fields")
	}
}
func TestV1CommandsRejectUnboundConfiguration(t *testing.T) {
	for _, field := range []string{`"workspace_mount_path":"/work"`, `"env":[{"name":"APP_MODE","value":"secret"}]`, `"labels":{"team":"ml"}`, `"volume_mounts":[{"pvc_name":"datasets","mount_path":"/data"}]`, `"instance_contract_version":9`} {
		spec := currentBusiness()
		raw, _ := protojson.Marshal(spec)
		raw = append(raw[:len(raw)-1], []byte(","+field+"}")...)
		if err := protojson.Unmarshal(raw, spec); err != nil {
			t.Fatal(err)
		}
		if _, err := CommandDigest(&CommandContext{}, spec); err == nil {
			t.Fatal("v1 accepted configuration outside digest")
		}
		if _, err := DecodeBusinessPayload(raw); err == nil {
			t.Fatal("v1 decoder accepted configuration")
		}
	}
}
func TestV2CommandCoversConfigurationAndEnvOrder(t *testing.T) {
	spec := currentBusiness()
	spec.InstanceContractVersion = 2
	spec.WorkspaceMountPath = "/work"
	spec.Labels = map[string]string{"team": "ml"}
	spec.Env = []*InstanceEnvVar{{Name: "A", Value: " a "}, {Name: "B", Value: "$(A)"}}
	c := &CommandContext{TenantId: "tenant", ResourceId: "instance", OperationId: "operation"}
	want, err := CommandDigest(c, spec)
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*CreateSpec){func(s *CreateSpec) { s.Env[0], s.Env[1] = s.Env[1], s.Env[0] }, func(s *CreateSpec) { s.Env[0].Value = "a" }, func(s *CreateSpec) { s.WorkspaceMountPath = "/other" }, func(s *CreateSpec) { s.Labels["team"] = "ops" }} {
		other := proto.Clone(spec).(*CreateSpec)
		change(other)
		got, err := CommandDigest(c, other)
		if err != nil || got == want {
			t.Fatal("configuration not bound", err)
		}
	}
	old := proto.Clone(spec).(*CreateSpec)
	old.InstanceContractVersion = 1
	old.WorkspaceMountPath = ""
	old.Labels = nil
	old.Env = nil
	v1, err := CommandDigest(c, old)
	if err != nil || v1 == want {
		t.Fatal("version digest domain not separated", err)
	}
}

func TestV1NestedCanonicalFieldProjection(t *testing.T) {
	spec := currentBusiness()
	spec.GpuExecution = &acc.GpuExecution{}
	spec.GpuDisplay = &acc.GpuDisplay{Mode: acc.SupplyMode_SHARED_FIXED, ProfileVersion: 2}
	spec.GpuSelection = &GpuPresetReference{Mode: acc.SupplyMode_WHOLE_EXCLUSIVE, ProfileVersion: 3}
	raw, err := CanonicalBusinessPayload(spec)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err = json.Unmarshal(raw, &fields); err != nil {
		t.Fatal(err)
	}
	golden := map[string]string{
		"gpu_execution": `{"execution_digest":"","node_selector":{},"pod_annotations":{},"queue_name":"","resources":{},"runtime_class_name":"","scheduler_name":""}`,
		"gpu_display":   `{"mode":"SHARED_FIXED","model_key":"","profile_id":"","profile_name":"","profile_version":"2"}`,
		"gpu_selection": `{"cluster_id":"","core_limit_percent":0,"devices_per_instance":0,"memory_mib_per_device":"0","mode":"WHOLE_EXCLUSIVE","model_key":"","pool_id":"","profile_id":"","profile_version":"3","spec_digest":""}`,
		"scheduling":    `{"pod_placement":"NONE"}`,
	}
	for field, want := range golden {
		if string(fields[field]) != want {
			t.Fatalf("legacy %s bytes changed: %s", field, fields[field])
		}
	}
}
