package instancev1

import (
	"testing"

	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
)

func TestCanonicalBusinessLegacyGPUBytes(t *testing.T) {
	// Fixed bytes follow the pre-instance_type schema and HEAD's canonical
	// encoding contract, including protobuf int64 strings and sorted JSON keys.
	business := &CreateBusiness{Data: &CreateInstanceData{Name: "legacy", ImageId: "image", ImageScope: "platform", PresetId: "shared", PresetVersion: 1}, Spec: &FrozenSpec{ImageReference: "registry.invalid/work@sha256:abc", ImageDigest: "sha256:abc", Cpu: "1", Memory: "2Gi", ClusterId: "cluster", Namespace: "tenant", ServiceAccount: "workload", StorageClass: "ani-block", WorkspaceSize: "1Gi", Shell: "/bin/bash", Uid: 1000, Gid: 1000}, Gpu: &acc.GpuRequest{ClusterId: "cluster", PoolId: "pool", ProfileId: "profile", ProfileVersion: 7, Replicas: 1, DevicesPerReplica: 1, ContainerName: "workspace"}}
	const expected = `{"data":{"image_id":"image","image_scope":"platform","name":"legacy","preset_id":"shared","preset_version":"1"},"gpu":{"cluster_id":"cluster","container_name":"workspace","devices_per_replica":1,"pool_id":"pool","profile_id":"profile","profile_version":"7","replicas":1},"spec":{"cluster_id":"cluster","cpu":"1","gid":"1000","image_digest":"sha256:abc","image_reference":"registry.invalid/work@sha256:abc","memory":"2Gi","namespace":"tenant","service_account":"workload","shell":"/bin/bash","storage_class":"ani-block","uid":"1000","workspace_size":"1Gi"}}`
	raw, err := CanonicalBusinessPayload(business)
	if err != nil || string(raw) != expected {
		t.Fatalf("legacy GPU canonical bytes changed; replay digest would drift: %s; error=%v", raw, err)
	}
	decoded, err := DecodeBusinessPayload([]byte(expected))
	if err != nil {
		t.Fatal("historical GPU payload no longer decodes", err)
	}
	replayed, err := CanonicalBusinessPayload(decoded)
	if err != nil || string(replayed) != expected {
		t.Fatalf("historical GPU replay changed canonical bytes: %s; error=%v", replayed, err)
	}
}

func TestCanonicalBusinessExplicitCPUType(t *testing.T) {
	business := &CreateBusiness{Spec: &FrozenSpec{InstanceType: "cpu"}}
	const expected = `{"data":null,"gpu":null,"spec":{"cluster_id":"","cpu":"","gid":"0","image_digest":"","image_reference":"","instance_type":"cpu","memory":"","namespace":"","service_account":"","shell":"","storage_class":"","uid":"0","workspace_size":""}}`
	raw, err := CanonicalBusinessPayload(business)
	if err != nil || string(raw) != expected {
		t.Fatalf("explicit CPU type missing from canonical business: %s; error=%v", raw, err)
	}
}
