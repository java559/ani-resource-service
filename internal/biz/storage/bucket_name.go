package storage

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
	"time"
)

const (
	defaultMaxBucketsPerTenant = 20
	defaultMaxUploadBytes      = int64(2 << 30)
	minimumUploadBytes         = int64(1 << 20)
	maximumUploadBytes         = int64(5 << 30)
	defaultUploadTTL           = 15 * time.Minute
	maximumUploadTTL           = time.Hour
)

var (
	tenantBucketIDPattern     = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$`)
	tenantBucketPrefixPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,9}$`)
	s3BucketPattern           = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$`)
)

// ValidateBucketID 校验租户可创建的逻辑桶名；物理名由服务端拼接。
func ValidateBucketID(id string) error {
	if !tenantBucketIDPattern.MatchString(id) || strings.Contains(id, "--") {
		return ErrInvalid
	}
	return nil
}

// ValidateTenantBucketPrefix 校验建桶前缀；空串表示关闭租户建桶。
func ValidateTenantBucketPrefix(prefix string) error {
	if prefix == "" {
		return nil
	}
	if !tenantBucketPrefixPattern.MatchString(prefix) {
		return ErrInvalid
	}
	return nil
}

// TenantBucketName 推导物理桶名：前缀 + cluster/tenant 指纹 + 逻辑名。
// 加入 cluster_id 防止多个集群共用同一 S3 时同一租户 UUID 撞名。
func TenantBucketName(prefix, clusterID, tenant, id string) (string, error) {
	if err := ValidateTenantBucketPrefix(prefix); err != nil || prefix == "" {
		return "", ErrInvalid
	}
	if err := ValidateBucketID(id); err != nil {
		return "", err
	}
	tenantPrefix, err := tenantBucketPrefix(prefix, clusterID, tenant)
	if err != nil {
		return "", err
	}
	name := tenantPrefix + id
	if len(name) > 63 || !s3BucketPattern.MatchString(name) {
		return "", ErrInvalid
	}
	return name, nil
}

func tenantBucketPrefix(prefix, clusterID, tenant string) (string, error) {
	if err := ValidateTenantBucketPrefix(prefix); err != nil || prefix == "" {
		return "", ErrInvalid
	}
	if !CanonicalUUID(tenant) || strings.TrimSpace(clusterID) == "" {
		return "", ErrInvalid
	}
	sum := sha256.Sum256([]byte(clusterID + "|" + tenant))
	fingerprint := hex.EncodeToString(sum[:])[:20]
	return prefix + fingerprint + "-", nil
}
