package biz

import "testing"

func TestVirtualSwitchPreservesLegacyVlanFingerprint(t *testing.T) {
	i := PlatformIntent{Kind: "create_vlan", Name: "legacy", IdempotencyKey: "ignored", Vlan: &VlanConfig{DeviceID: "device_11111111111111111111111111111111", VlanID: 0}}
	// Captured from the pre-v2 PlatformIntent JSON encoding. The new optional
	// fields must not change accepted v1 keys or require a new provider lookup.
	const golden = "f56256acde4abe1fd657c440a73a774f43d3141948c19edb8df77d19ea5c3698"
	if i.FingerprintVersion() != 1 || i.VersionedFingerprint() != golden {
		t.Fatalf("legacy intent encoding changed: %s", i.VersionedFingerprint())
	}
	newIntent := PlatformIntent{Kind: "create_vlan", Name: "v2", IdempotencyKey: "key", Vlan: &VlanConfig{ClusterID: "test-cluster", VirtualSwitchID: "vsw_11111111111111111111111111111111", DeviceName: "ens35", VlanID: 0}}
	fingerprint := newIntent.VersionedFingerprint()
	newIntent.IdempotencyKey = "another-key"
	newIntent.Vlan.DeviceID = "device_server_generated"
	newIntent.Vlan.GroupName = "derived-name"
	newIntent.Vlan.Nodes = []VirtualSwitchNode{{State: "applied"}}
	if newIntent.FingerprintVersion() != 2 || newIntent.VersionedFingerprint() != fingerprint {
		t.Fatal("server observations changed v2 acceptance identity")
	}
	newIntent.Vlan.DeviceName = "ens36"
	if newIntent.VersionedFingerprint() == fingerprint {
		t.Fatal("v2 request identity omitted device_name")
	}
}
