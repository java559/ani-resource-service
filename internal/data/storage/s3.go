package storage

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	awshttp "github.com/aws/smithy-go/transport/http"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/storage"
)

// S3Config 是对象存储的私有配置；endpoint 为空表示本期不启用对象浏览。
type S3Config struct {
	Endpoint        string
	BrowserEndpoint string
	Region          string
	PathStyle       bool
	CredentialsFile string
	CAFile          string
}

// S3 使用两个客户端：内部地址做 List/HEAD，浏览器地址做预签名。
type S3 struct {
	internal *s3.Client
	presign  *s3.PresignClient
	browser  bool
}

func NewS3(c S3Config) (*S3, error) {
	if c.Endpoint == "" {
		return nil, nil
	}
	internalEndpoint, err := endpointURL(c.Endpoint)
	if err != nil {
		return nil, fmt.Errorf("object_storage.endpoint: %w", err)
	}
	browserConfigured := c.BrowserEndpoint != ""
	browserEndpoint := internalEndpoint
	if browserConfigured {
		if browserEndpoint, err = endpointURL(c.BrowserEndpoint); err != nil {
			return nil, fmt.Errorf("object_storage.browser_endpoint: %w", err)
		}
	}
	accessKey, secretKey, err := readCredentials(c.CredentialsFile)
	if err != nil {
		return nil, err
	}
	httpClient, err := s3HTTPClient(c.CAFile)
	if err != nil {
		return nil, err
	}
	provider := credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")
	internal := s3.New(s3.Options{
		Region:       c.Region,
		Credentials:  provider,
		BaseEndpoint: aws.String(internalEndpoint),
		UsePathStyle: c.PathStyle,
		HTTPClient:   httpClient,
	})
	browser := internal
	if browserConfigured && browserEndpoint != internalEndpoint {
		browser = s3.New(s3.Options{
			Region:       c.Region,
			Credentials:  provider,
			BaseEndpoint: aws.String(browserEndpoint),
			UsePathStyle: c.PathStyle,
			HTTPClient:   httpClient,
		})
	}
	return &S3{internal: internal, presign: s3.NewPresignClient(browser), browser: browserConfigured}, nil
}

func endpointURL(raw string) (string, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("absolute http(s) endpoint required")
	}
	return strings.TrimSuffix(parsed.String(), "/"), nil
}

func readCredentials(path string) (string, string, error) {
	if path == "" {
		return "", "", fmt.Errorf("object_storage.credentials_file required")
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return "", "", fmt.Errorf("object storage credentials unavailable")
	}
	defer clear(body)
	var accessKey, secretKey string
	for _, line := range strings.Split(string(body), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, found := strings.Cut(line, "=")
		if !found {
			continue
		}
		switch strings.TrimSpace(key) {
		case "access_key":
			accessKey = strings.TrimSpace(value)
		case "secret_key":
			secretKey = strings.TrimSpace(value)
		}
	}
	if accessKey == "" || secretKey == "" {
		return "", "", fmt.Errorf("object storage credentials require access_key and secret_key")
	}
	return accessKey, secretKey, nil
}

func s3HTTPClient(caFile string) (*http.Client, error) {
	config := &tls.Config{MinVersion: tls.VersionTLS12}
	if caFile != "" {
		pem, err := os.ReadFile(caFile)
		if err != nil {
			return nil, fmt.Errorf("object storage CA unavailable")
		}
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM(pem) {
			return nil, fmt.Errorf("object storage CA invalid")
		}
		config.RootCAs = roots
	}
	// 不支持跳过证书校验：没有 InsecureSkipVerify 开关。
	return &http.Client{
		Transport: &http.Transport{TLSClientConfig: config, ForceAttemptHTTP2: true},
		Timeout:   30 * time.Second,
	}, nil
}

func (s *S3) Available() bool {
	return s != nil && s.presign != nil && s.browser
}

func (s *S3) ListBuckets(ctx context.Context) ([]biz.BucketInfo, error) {
	pages := s3.NewListBucketsPaginator(s.internal, &s3.ListBucketsInput{})
	infos := []biz.BucketInfo{}
	for pages.HasMorePages() {
		out, err := pages.NextPage(ctx)
		if err != nil {
			return nil, s3Error(err)
		}
		for _, bucket := range out.Buckets {
			if bucket.Name == nil {
				continue
			}
			info := biz.BucketInfo{Name: *bucket.Name}
			if bucket.CreationDate != nil {
				info.CreatedAt = *bucket.CreationDate
			}
			infos = append(infos, info)
		}
	}
	return infos, nil
}

// BucketStats 逐页列举对象并累加数量与字节数，达到上限即停止并标记不完整。
func (s *S3) BucketStats(ctx context.Context, bucket string, maxObjects int64) (biz.ObjectStats, error) {
	stats := biz.ObjectStats{}
	pages := s3.NewListObjectsV2Paginator(s.internal, &s3.ListObjectsV2Input{
		Bucket: aws.String(bucket),
	})
	for pages.HasMorePages() {
		out, err := pages.NextPage(ctx)
		if err != nil {
			return biz.ObjectStats{}, s3Error(err)
		}
		for _, item := range out.Contents {
			stats.ObjectCount++
			stats.TotalSize += aws.ToInt64(item.Size)
			stats.Scanned++
			if maxObjects > 0 && stats.Scanned >= maxObjects {
				stats.Truncated = true
				return stats, nil
			}
		}
	}
	return stats, nil
}

func (s *S3) CreateBucket(ctx context.Context, bucket string) error {
	_, err := s.internal.CreateBucket(ctx, &s3.CreateBucketInput{Bucket: aws.String(bucket)})
	if err == nil {
		return nil
	}
	var apiErr interface{ ErrorCode() string }
	if errors.As(err, &apiErr) && apiErr.ErrorCode() == "BucketAlreadyOwnedByYou" {
		return nil
	}
	return s3Error(err)
}

func (s *S3) HeadBucket(ctx context.Context, bucket string) error {
	if _, err := s.internal.HeadBucket(ctx, &s3.HeadBucketInput{Bucket: aws.String(bucket)}); err != nil {
		return s3Error(err)
	}
	return nil
}

func (s *S3) DeleteObject(ctx context.Context, bucket, key, expectedETag string) error {
	input := &s3.DeleteObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)}
	if expectedETag != "" {
		input.IfMatch = aws.String(`"` + biz.NormalizeETag(expectedETag) + `"`)
	}
	if _, err := s.internal.DeleteObject(ctx, input); err != nil {
		return s3Error(err)
	}
	return nil
}

func (s *S3) ListObjects(ctx context.Context, bucket, prefix, continuationToken string, limit int32) (biz.ObjectPage, error) {
	input := &s3.ListObjectsV2Input{
		Bucket:    aws.String(bucket),
		Prefix:    aws.String(prefix),
		Delimiter: aws.String("/"),
		MaxKeys:   aws.Int32(limit),
	}
	if continuationToken != "" {
		input.ContinuationToken = aws.String(continuationToken)
	}
	out, err := s.internal.ListObjectsV2(ctx, input)
	if err != nil {
		return biz.ObjectPage{}, s3Error(err)
	}
	page := biz.ObjectPage{IsTruncated: aws.ToBool(out.IsTruncated)}
	for _, item := range out.Contents {
		entry := biz.ObjectItem{Key: aws.ToString(item.Key), Size: aws.ToInt64(item.Size)}
		if item.ETag != nil {
			entry.ETag = *item.ETag
		}
		if item.LastModified != nil {
			entry.LastModified = *item.LastModified
		}
		page.Items = append(page.Items, entry)
	}
	for _, prefix := range out.CommonPrefixes {
		page.CommonPrefixes = append(page.CommonPrefixes, aws.ToString(prefix.Prefix))
	}
	if page.IsTruncated && out.NextContinuationToken != nil {
		page.NextToken = *out.NextContinuationToken
	}
	return page, nil
}

func (s *S3) HeadObject(ctx context.Context, bucket, key string) (biz.ObjectMeta, error) {
	out, err := s.internal.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)})
	if err != nil {
		return biz.ObjectMeta{}, s3Error(err)
	}
	meta := biz.ObjectMeta{Key: key, Size: aws.ToInt64(out.ContentLength)}
	if out.ETag != nil {
		meta.ETag = *out.ETag
	}
	if out.ContentType != nil {
		meta.ContentType = *out.ContentType
	}
	if out.LastModified != nil {
		meta.LastModified = *out.LastModified
	}
	return meta, nil
}

func (s *S3) PresignGetObject(ctx context.Context, bucket, key string, expiry time.Duration) (string, time.Time, error) {
	presigned, err := s.presign.PresignGetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)}, s3.WithPresignExpires(expiry))
	if err != nil {
		return "", time.Time{}, s3Error(err)
	}
	return presigned.URL, time.Now().Add(expiry), nil
}

func (s *S3) PresignPutObject(ctx context.Context, bucket, key, contentType string, overwrite bool, expiry time.Duration) (string, map[string]string, time.Time, error) {
	if s == nil || s.presign == nil {
		return "", nil, time.Time{}, biz.ErrUploadUnavailable
	}
	input := &s3.PutObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)}
	if contentType != "" {
		input.ContentType = aws.String(contentType)
	}
	if !overwrite {
		input.IfNoneMatch = aws.String("*")
	}
	presigned, err := s.presign.PresignPutObject(ctx, input, s3.WithPresignExpires(expiry))
	if err != nil {
		return "", nil, time.Time{}, s3Error(err)
	}
	headers := map[string]string{}
	for name, values := range presigned.SignedHeader {
		if !strings.EqualFold(name, "host") {
			headers[name] = strings.Join(values, ",")
		}
	}
	if contentType != "" {
		headers["Content-Type"] = contentType
	}
	return presigned.URL, headers, time.Now().Add(expiry), nil
}

func s3Error(err error) error {
	if err == nil {
		return nil
	}
	var apiErr interface{ ErrorCode() string }
	if errors.As(err, &apiErr) {
		switch apiErr.ErrorCode() {
		case "NoSuchKey", "NoSuchBucket", "NotFound", "404":
			return biz.ErrNotFound
		case "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "403":
			return biz.ErrAccessDenied
		case "BucketAlreadyExists":
			return biz.ErrBucketExists
		case "EntityTooLarge":
			return biz.ErrObjectTooLarge
		case "InvalidBucketName":
			return biz.ErrInvalid
		case "PreconditionFailed", "412":
			return biz.ErrObjectIdentity
		case "ConditionalRequestConflict":
			return biz.ErrConflict
		}
	}
	var responseErr *awshttp.ResponseError
	if errors.As(err, &responseErr) {
		switch responseErr.HTTPStatusCode() {
		case http.StatusNotFound:
			return biz.ErrNotFound
		case http.StatusForbidden:
			return biz.ErrAccessDenied
		case http.StatusPreconditionFailed:
			return biz.ErrObjectIdentity
		}
	}
	return fmt.Errorf("%w: %v", biz.ErrBackendUnavailable, err)
}

var _ biz.ObjectPort = (*S3)(nil)
