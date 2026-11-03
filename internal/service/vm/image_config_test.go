package vm

import (
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/vm/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

// TestImageUploadConfigAcceptsDigestPinnedValidator pins the reference shape the
// deployment path depends on. The off-by-one in dataImageNative only surfaced when
// ImageUpload.Enabled was switched to true on the cluster: with the switch off the
// branch never ran, so the service started and every management call still worked.
func TestImageUploadConfigAcceptsDigestPinnedValidator(t *testing.T) {
	good := "10.10.1.66:30003/ani-gpu-platform/ani-vm-image-verify@sha256:" + strings.Repeat("a", 64)
	if !dataImageNative(good) {
		t.Fatalf("digest-pinned validator image rejected: %s", good)
	}
	for _, bad := range []string{"", "alpine:3.22", "latest", good[:len(good)-1], strings.TrimPrefix(good, "10.10.1.66:30003/ani-gpu-platform/ani-vm-image-verify"),
		"repo@sha512:" + strings.Repeat("b", 64), "repo@sha256:" + strings.Repeat("c", 63)} {
		if dataImageNative(bad) {
			t.Errorf("invalid image reference accepted: %q", bad)
		}
	}
	upload := ImageUploadConfig{Enabled: true, ExternalUploadOrigin: "https://cdi-uploadproxy:30471",
		ValidatorImage: good, UploadDeadline: "24h", TokenTTL: "5m"}
	deadline, ttl, e := upload.Durations()
	if e != nil || deadline <= ttl {
		t.Fatalf("durations = %s/%s (%v)", deadline, ttl, e)
	}
	for _, bad := range []ImageUploadConfig{{UploadDeadline: "24h", TokenTTL: "24h"}, {UploadDeadline: "0"}, {TokenTTL: "-1m"},
		{UploadDeadline: "24x", TokenTTL: "5m"}, {UploadDeadline: "", TokenTTL: "bad"}} {
		if _, _, e := bad.Durations(); !errors.Is(e, biz.ErrImageInvalid) {
			t.Errorf("invalid durations accepted: %+v -> %v", bad, e)
		}
	}
	if _, e := NewImageService(nil, nil, nil, upload, "cluster", nil); !errors.Is(e, biz.ErrImageInvalid) {
		t.Errorf("incomplete wiring accepted: %v", e)
	}
	off := upload
	off.Enabled = false
	off.ValidatorImage = ""
	if _, e := NewImageService(nil, nil, nil, off, "cluster", nil); !errors.Is(e, biz.ErrImageInvalid) {
		t.Errorf("nil ports accepted while disabled: %v", e)
	}
}

// TestNewUploadedImageRecordShapesTheRowTheDomainExpects is the regression for the create
// path that returned VM_IMAGE_INVALID_ARGUMENT for every tenant on the cluster.
func TestNewUploadedImageRecordShapesTheRowTheDomainExpects(t *testing.T) {
	tenant, id := uuid.NewString(), uuid.NewString()
	profile, e := biz.ImageOSProfile("linux", "24.04", "amd64")
	if e != nil {
		t.Fatal(e)
	}
	request := &pb.CreateImageRequest{TenantId: tenant, Kind: "DISK_IMAGE", Format: "qcow2", Name: " e2e qcow2 ",
		Description: "kept", SourceCapacityGb: 8}
	record, e := newUploadedImageRecord(request, id, profile, "admin")
	if e != nil {
		t.Fatalf("valid create rejected: %v", e)
	}
	if record.MediaKey != biz.ImageMediaKey(id) || record.State != biz.StatePreparing ||
		record.CleanupState != biz.CleanupNone || record.Version != 1 {
		t.Errorf("record identity/state not stamped: %+v", record)
	}
	if record.Name != "e2e qcow2" {
		t.Errorf("name not trimmed: %q", record.Name)
	}
	if record.CreatedBy != "admin" {
		t.Errorf("actor not recorded as creator: %q", record.CreatedBy)
	}
	for _, bad := range []*pb.CreateImageRequest{{TenantId: tenant, Kind: "DISK_IMAGE", Format: "qcow2", SourceCapacityGb: 8},
		{TenantId: tenant, Kind: "DISK_IMAGE", Format: "qcow2", Name: strings.Repeat("n", 81), SourceCapacityGb: 8},
		{TenantId: tenant, Kind: "DISK_IMAGE", Format: "qcow2", Name: "x", SourceCapacityGb: 0},
		{TenantId: tenant, Kind: "INSTALL_ISO", Format: "qcow2", Name: "x", SourceCapacityGb: 8},
		{TenantId: "not-a-uuid", Kind: "DISK_IMAGE", Format: "qcow2", Name: "x", SourceCapacityGb: 8}} {
		if _, e := newUploadedImageRecord(bad, id, profile, "admin"); !errors.Is(e, biz.ErrImageInvalid) {
			t.Errorf("invalid record accepted: %+v -> %v", bad, e)
		}
	}
}

// TestValidateStaticMediaKeysKeepsReservedPrefix is the startup guard: a static media
// key that already uses the image prefix would let a catalog entry shadow a tenant image.
func TestValidateStaticMediaKeysKeepsReservedPrefix(t *testing.T) {
	if e := ValidateStaticMediaKeys(nil); e != nil {
		t.Errorf("empty catalog rejected: %v", e)
	}
	if e := ValidateStaticMediaKeys([]*pb.MediaReference{{MediaKey: "ubuntu-24.04-cloud"}, {MediaKey: "virtio-win-0.1.271"}}); e != nil {
		t.Errorf("clean catalog rejected: %v", e)
	}
	conflict := []*pb.MediaReference{{MediaKey: biz.ImageMediaKey(strings.Repeat("d", 32))}}
	if e := ValidateStaticMediaKeys(conflict); !errors.Is(e, biz.ErrImageInvalid) && e == nil {
		t.Errorf("reserved prefix accepted: %v", e)
	} else if e != nil && !strings.Contains(e.Error(), biz.ImageMediaKeyPrefix) {
		t.Errorf("conflict must name the offending key, got %v", e)
	}
}
