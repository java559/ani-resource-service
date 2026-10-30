package storage

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func newWriteUseCase(t *testing.T, objects ObjectPort, mutate func(*Config)) *UseCase {
	t.Helper()
	cfg := Config{
		NamespacePrefix:     testPrefix,
		ClusterID:           "cluster-a",
		DeleteExecutor:      "storage-tests",
		Buckets:             []BucketConfig{{ID: "files", TenantID: testTenant, Name: "文件", Bucket: "tenant-files"}},
		TenantBucketPrefix:  "ani-t-",
		MaxBucketsPerTenant: 20,
	}
	if mutate != nil {
		mutate(&cfg)
	}
	uc, err := NewUseCase(newFake(), objects, cfg)
	if err != nil {
		t.Fatal(err)
	}
	return uc
}

func physicalBucket(t *testing.T, tenant, id string) string {
	t.Helper()
	name, err := TenantBucketName("ani-t-", "cluster-a", tenant, id)
	if err != nil {
		t.Fatal(err)
	}
	return name
}

func TestTenantBucketName(t *testing.T) {
	name, err := TenantBucketName("ani-t-", "cluster-a", testTenant, "project-a")
	if err != nil || !strings.HasPrefix(name, "ani-t-") || !strings.HasSuffix(name, "-project-a") || len(name) > 63 {
		t.Fatalf("unexpected name %q, %v", name, err)
	}
	again, _ := TenantBucketName("ani-t-", "cluster-a", testTenant, "project-a")
	if name != again {
		t.Fatal("bucket name must be deterministic")
	}
	otherCluster, _ := TenantBucketName("ani-t-", "cluster-b", testTenant, "project-a")
	otherTenant, _ := TenantBucketName("ani-t-", "cluster-a", otherTenant, "project-a")
	if name == otherCluster || name == otherTenant {
		t.Fatal("cluster and tenant must change the fingerprint")
	}
	for _, id := range []string{"", "Project", "a_b", "-a", "a-", "a--b", strings.Repeat("a", 33)} {
		if _, err := TenantBucketName("ani-t-", "cluster-a", testTenant, id); !errors.Is(err, ErrInvalid) {
			t.Fatalf("id %q must be rejected", id)
		}
	}
	for _, prefix := range []string{"Ani-", "ani_t", strings.Repeat("a", 11)} {
		if _, err := TenantBucketName(prefix, "cluster-a", testTenant, "project-a"); !errors.Is(err, ErrInvalid) {
			t.Fatalf("prefix %q must be rejected", prefix)
		}
	}
}

func TestCreateBucketIdempotentAndLimit(t *testing.T) {
	objects := &fakeObjects{}
	uc := newWriteUseCase(t, objects, nil)
	bucket, err := uc.CreateBucket(context.Background(), testTenant, "project-a")
	if err != nil || bucket.ID != "project-a" || objects.lastCreated != physicalBucket(t, testTenant, "project-a") {
		t.Fatalf("create = %+v / %q / %v", bucket, objects.lastCreated, err)
	}
	before := len(objects.buckets)
	if _, err := uc.CreateBucket(context.Background(), testTenant, "project-a"); err != nil || len(objects.buckets) != before {
		t.Fatalf("repeat create must be idempotent: %v", err)
	}

	limited := newWriteUseCase(t, objects, func(c *Config) { c.MaxBucketsPerTenant = 1 })
	if _, err := limited.CreateBucket(context.Background(), testTenant, "project-b"); !errors.Is(err, ErrBucketLimit) {
		t.Fatalf("limit must reject: %v", err)
	}

	disabled := newWriteUseCase(t, objects, func(c *Config) { c.TenantBucketPrefix = "" })
	if _, err := disabled.CreateBucket(context.Background(), testTenant, "project-a"); !errors.Is(err, ErrBucketDisabled) {
		t.Fatalf("disabled must reject: %v", err)
	}
	if _, err := uc.CreateBucket(context.Background(), testTenant, "files"); !errors.Is(err, ErrBucketExists) {
		t.Fatalf("configured bucket must conflict: %v", err)
	}
}

func TestUploadURLRules(t *testing.T) {
	base := &fakeObjects{available: true, headErr: ErrNotFound}
	uc := newWriteUseCase(t, base, nil)
	ticket, err := uc.UploadURL(context.Background(), testTenant, "project-a", "docs/a.txt", "text/plain", 12, false)
	if err != nil || ticket.URL == "" || ticket.Headers["Content-Type"] != "text/plain" || ticket.Key != "docs/a.txt" {
		t.Fatalf("ticket = %+v, %v", ticket, err)
	}
	// project-a 桶尚未在 fake 中；HeadBucket 默认为 nil，模拟已存在。

	if _, err := uc.UploadURL(context.Background(), testTenant, "project-a", "docs/a.txt", "", 12, true); err != nil {
		t.Fatalf("empty content type must default: %v", err)
	}
	for _, tc := range []struct {
		key   string
		size  int64
		code  error
		over  bool
		setup func(*fakeObjects)
	}{
		{"docs/a.txt", 0, ErrInvalid, false, nil},
		{"docs/a.txt", defaultMaxUploadBytes + 1, ErrObjectTooLarge, false, nil},
		{"/docs/a.txt", 1, ErrInvalid, false, nil},
		{"docs/", 1, ErrInvalid, false, nil},
		{"docs/a.txt", 1, ErrObjectExists, false, func(f *fakeObjects) { f.headErr = nil }},
		{"docs/a.txt", 1, ErrNotFound, false, func(f *fakeObjects) { f.headErr = nil; f.headBucketErr = ErrNotFound }},
	} {
		objects := &fakeObjects{available: true, headErr: ErrNotFound}
		if tc.setup != nil {
			tc.setup(objects)
		}
		uc := newWriteUseCase(t, objects, nil)
		if _, err := uc.UploadURL(context.Background(), testTenant, "project-a", tc.key, "", tc.size, tc.over); !errors.Is(err, tc.code) {
			t.Fatalf("key=%q size=%d: got %v, want %v", tc.key, tc.size, err, tc.code)
		}
	}
	unavailable := newWriteUseCase(t, &fakeObjects{available: false}, nil)
	if _, err := unavailable.UploadURL(context.Background(), testTenant, "project-a", "docs/a.txt", "", 1, false); !errors.Is(err, ErrUploadUnavailable) {
		t.Fatalf("unavailable must reject: %v", err)
	}
}

func TestDeleteObjectStates(t *testing.T) {
	objects := &fakeObjects{available: true, meta: ObjectMeta{Key: "docs/a.txt", ETag: `"abc"`}}
	uc := newWriteUseCase(t, objects, nil)
	state, err := uc.DeleteObject(context.Background(), testTenant, "project-a", "docs/a.txt", "abc")
	if err != nil || state != ObjectDeleted || len(objects.deleted) != 1 {
		t.Fatalf("delete = %q, %v, deleted=%v", state, err, objects.deleted)
	}
	if _, err := uc.DeleteObject(context.Background(), testTenant, "project-a", "docs/a.txt", "different"); !errors.Is(err, ErrObjectIdentity) {
		t.Fatalf("etag mismatch must reject: %v", err)
	}
	missing := &fakeObjects{available: true, headErr: ErrNotFound}
	uc = newWriteUseCase(t, missing, nil)
	if state, err := uc.DeleteObject(context.Background(), testTenant, "project-a", "docs/a.txt", ""); err != nil || state != ObjectGone {
		t.Fatalf("missing = %q, %v", state, err)
	}
}

func TestBucketWriteIsolation(t *testing.T) {
	objects := &fakeObjects{available: true, createErr: ErrBucketExists}
	uc := newWriteUseCase(t, objects, nil)
	if _, err := uc.CreateBucket(context.Background(), testTenant, "external"); !errors.Is(err, ErrBucketExists) {
		t.Fatalf("foreign bucket adopted: %v", err)
	}
	physical := physicalBucket(t, testTenant, "foreign")
	uc = newWriteUseCase(t, objects, func(c *Config) {
		c.Buckets = append(c.Buckets, BucketConfig{ID: "owned-by-other", TenantID: otherTenant, Name: "foreign", Bucket: physical})
	})
	if _, err := uc.CreateBucket(context.Background(), testTenant, "foreign"); !errors.Is(err, ErrBucketExists) {
		t.Fatal(err)
	}
	if _, err := uc.bucket(testTenant, "foreign"); !errors.Is(err, ErrNotFound) {
		t.Fatal("cross-tenant bucket resolved")
	}
	objects.buckets = []string{physical}
	items, err := uc.ListBuckets(context.Background(), testTenant, BucketFilter{})
	if err != nil || len(items) != 1 {
		t.Fatalf("foreign bucket visible: %+v %v", items, err)
	}
	for _, tenant := range []string{"", "not-a-uuid"} {
		if _, err := uc.ListBuckets(context.Background(), tenant, BucketFilter{}); !errors.Is(err, ErrInvalid) {
			t.Fatal("invalid tenant accepted")
		}
	}
	objects.headBucketErr = ErrNotFound
	if _, err := uc.DeleteObject(context.Background(), testTenant, "absent", "a.txt", ""); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing bucket became GONE: %v", err)
	}
	objects.buckets = []string{physicalBucket(t, testTenant, "files")}
	if _, err := uc.ListBuckets(context.Background(), testTenant, BucketFilter{}); !errors.Is(err, ErrConflict) {
		t.Fatal("ambiguous logical bucket ID accepted")
	}
}

func TestBucketMetadataAndStats(t *testing.T) {
	objects := &fakeObjects{stats: ObjectStats{ObjectCount: 2, TotalSize: 30, Scanned: 2}}
	uc := newWriteUseCase(t, objects, func(c *Config) { c.Region = "us-east-1" })
	bucket, err := uc.CreateBucket(context.Background(), testTenant, "project-a")
	if err != nil || bucket.Region != "us-east-1" || bucket.StorageClass != StorageClassStandard || bucket.ACL != ACLPrivate {
		t.Fatalf("bucket metadata = %+v, %v", bucket, err)
	}
	stats, err := uc.BucketStats(context.Background(), testTenant, "project-a")
	if err != nil || stats.ObjectCount != 2 || stats.TotalSize != 30 || stats.Truncated {
		t.Fatalf("stats = %+v, %v", stats, err)
	}
}

func TestListBucketsMergesTenantBuckets(t *testing.T) {
	project := physicalBucket(t, testTenant, "project-a")
	foreign, _ := TenantBucketName("ani-t-", "cluster-a", otherTenant, "foreign")
	objects := &fakeObjects{buckets: []string{project, foreign, "someone-else"}}
	uc := newWriteUseCase(t, objects, nil)
	items, err := uc.ListBuckets(context.Background(), testTenant, BucketFilter{})
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 || items[0].ID != "files" || items[1].ID != "project-a" {
		t.Fatalf("items = %+v", items)
	}
	if items[1].Name != "project-a" {
		t.Fatalf("dynamic bucket name = %q", items[1].Name)
	}
}
