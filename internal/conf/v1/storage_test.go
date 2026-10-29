package conf

import (
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/durationpb"
	"path/filepath"
	"testing"
	"time"
)

func TestStorageWriteConfiguration(t *testing.T) {
	config := &ObjectStorage{Endpoint: "https://storage.example", Region: "us-east-1", CredentialsFile: filepath.Join(t.TempDir(), "credentials"), TenantBucketPrefix: "ani-t-", MaxBucketsPerTenant: 20, MaxUploadBytes: 2 << 30, UploadTtl: durationpb.New(15 * time.Minute)}
	if err := config.Validate(); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*ObjectStorage){
		"missing endpoint":  func(c *ObjectStorage) { c.Endpoint = "" },
		"prefix whitespace": func(c *ObjectStorage) { c.TenantBucketPrefix = " ani-t- " },
		"bucket limit":      func(c *ObjectStorage) { c.MaxBucketsPerTenant = 101 },
		"negative size":     func(c *ObjectStorage) { c.MaxUploadBytes = -1 },
		"oversized limit":   func(c *ObjectStorage) { c.MaxUploadBytes = 6 << 30 },
		"short ttl":         func(c *ObjectStorage) { c.UploadTtl = durationpb.New(time.Second) },
		"negative ttl":      func(c *ObjectStorage) { c.UploadTtl = durationpb.New(-time.Minute) },
		"long ttl":          func(c *ObjectStorage) { c.UploadTtl = durationpb.New(2 * time.Hour) },
	} {
		t.Run(name, func(t *testing.T) {
			c := proto.Clone(config).(*ObjectStorage)
			mutate(c)
			if c.Validate() == nil {
				t.Fatal("invalid config accepted")
			}
		})
	}
}
