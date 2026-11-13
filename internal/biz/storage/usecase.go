package storage

import (
	"context"
	"errors"
	"fmt"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/tenantnamespace"
	"log/slog"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	defaultPageSize    = 20
	maximumPageSize    = 100
	defaultLimit       = 100
	maximumLimit       = 100
	defaultDownloadTTL = 10 * time.Minute
)

// Config 是启动期配置；页面建桶不修改静态配置。
type Config struct {
	NamespacePrefix    string
	ClusterID          string
	DeleteExecutor     string
	BorrowedPVCGuard   BorrowedPVCGuard
	ObserveDeleteWrite func(string)
	Buckets            []BucketConfig
	Region             string
	DownloadTTL        time.Duration
	// TenantBucketPrefix 为空表示关闭租户建桶；非空时物理桶名由该前缀推导。
	TenantBucketPrefix  string
	MaxBucketsPerTenant int
	MaxUploadBytes      int64
	UploadTTL           time.Duration
	// Logger 为空时回落 slog.Default()；仅用于 C08 定义的诊断日志输出。
	Logger *slog.Logger
}

// UseCase 编排独立盘与对象存储，不直接依赖 Kratos、Proto 或 SDK。
type UseCase struct {
	volumes VolumePort
	objects ObjectPort
	cfg     Config
	now     func() time.Time
	logger  *slog.Logger
}

func NewUseCase(volumes VolumePort, objects ObjectPort, cfg Config) (*UseCase, error) {
	if volumes == nil || cfg.NamespacePrefix != "tenant-" {
		return nil, ErrInvalid
	}
	if err := ValidateBuckets(cfg.Buckets); err != nil {
		return nil, err
	}
	if cfg.DownloadTTL <= 0 || cfg.DownloadTTL > defaultDownloadTTL {
		cfg.DownloadTTL = defaultDownloadTTL
	}
	if cfg.MaxBucketsPerTenant == 0 {
		cfg.MaxBucketsPerTenant = defaultMaxBucketsPerTenant
	}
	if cfg.MaxBucketsPerTenant < 1 || cfg.MaxBucketsPerTenant > 100 {
		return nil, ErrInvalid
	}
	if cfg.MaxUploadBytes == 0 {
		cfg.MaxUploadBytes = defaultMaxUploadBytes
	}
	if cfg.MaxUploadBytes < minimumUploadBytes || cfg.MaxUploadBytes > maximumUploadBytes {
		return nil, ErrInvalid
	}
	if cfg.UploadTTL == 0 {
		cfg.UploadTTL = defaultUploadTTL
	}
	if cfg.UploadTTL < time.Minute || cfg.UploadTTL > maximumUploadTTL {
		return nil, ErrInvalid
	}
	if cfg.Region == "" {
		cfg.Region = "us-east-1"
	}
	if cfg.TenantBucketPrefix != "" {
		if err := ValidateTenantBucketPrefix(cfg.TenantBucketPrefix); err != nil || objects == nil || strings.TrimSpace(cfg.ClusterID) == "" {
			return nil, ErrInvalid
		}
	}
	logger := cfg.Logger
	if logger == nil {
		logger = slog.Default()
	}
	return &UseCase{volumes: volumes, objects: objects, cfg: cfg, now: time.Now, logger: logger}, nil
}

func (u *UseCase) namespace(tenant string) (string, error) {
	return NamespaceName(u.cfg.NamespacePrefix, tenant)
}

// ensureNamespace 校验租户命名空间归属与状态，并返回命名空间名。
// 现有卷的列表、详情、扩容、删除都经过这里，避免命名空间标签失效或进入删除中后继续操作。
func (u *UseCase) namespaceIdentity(ctx context.Context, tenant string) (Namespace, error) {
	name, err := u.namespace(tenant)
	if err != nil {
		return Namespace{}, err
	}
	ns, err := u.volumes.GetNamespace(ctx, name)
	if err != nil {
		return Namespace{}, err
	}
	if tenantnamespace.Validate(tenant, tenantnamespace.Object{Name: ns.Name, UID: ns.UID, Phase: ns.Phase, Deleting: ns.Deleting, Labels: map[string]string{tenantnamespace.TenantLabel: ns.TenantID, tenantnamespace.ManagedByLabel: ns.ManagedBy}}, "", true) != nil {
		return Namespace{}, ErrNotFound
	}
	return ns, nil
}
func (u *UseCase) ensureNamespace(ctx context.Context, tenant string) (string, error) {
	ns, err := u.namespaceIdentity(ctx, tenant)
	return ns.Name, err
}
func (u *UseCase) verifyNamespaceUID(ctx context.Context, tenant, uid string) error {
	ns, err := u.namespaceIdentity(ctx, tenant)
	if err != nil {
		return err
	}
	if ns.UID != uid {
		return ErrIdentityChanged
	}
	return nil
}

func owned(record *VolumeRecord, tenant string) bool {
	if record == nil {
		return false
	}
	return record.Labels[LabelManagedBy] == ManagedByValue && record.Labels[LabelTenantID] == tenant
}

// ListStorageClasses 返回全部可选 StorageClass，排序与分页由调用方负责。
func (u *UseCase) ListStorageClasses(ctx context.Context, tenant string) ([]Class, error) {
	if !CanonicalUUID(tenant) {
		return nil, ErrInvalid
	}
	items, err := u.volumes.ListStorageClasses(ctx)
	if err != nil {
		return nil, err
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Name < items[j].Name })
	return items, nil
}

// CreateVolume 使用 create_token 生成固定名称；只在未命中时执行首次创建前置检查。
func (u *UseCase) CreateVolume(ctx context.Context, tenant string, req CreateRequest) (Volume, error) {
	spec, err := NormalizeRequest(req.CreateToken, req.DisplayName, req.CapacityGB, req.StorageClassName, req.VolumeMode, req.AccessModes)
	if err != nil {
		return Volume{}, err
	}
	ns, err := u.namespaceIdentity(ctx, tenant)
	if err != nil {
		return Volume{}, err
	}
	namespace := ns.Name
	spec.NamespaceUID = ns.UID
	if existing, err := u.volumes.GetVolume(ctx, namespace, spec.Name); err == nil {
		return u.replayCreate(tenant, existing, spec)
	} else if !errors.Is(err, ErrNotFound) {
		return Volume{}, err
	}
	if err := u.verifyNamespaceUID(ctx, tenant, ns.UID); err != nil {
		return Volume{}, err
	}
	if _, err := u.volumes.GetStorageClass(ctx, spec.StorageClassName); err != nil {
		if errors.Is(err, ErrNotFound) {
			return Volume{}, ErrInvalid
		}
		return Volume{}, err
	}
	spec.Labels = map[string]string{
		LabelManagedBy:   ManagedByValue,
		LabelTenantID:    tenant,
		LabelCreateToken: req.CreateToken,
	}
	spec.Annotations = map[string]string{
		AnnotationName:   spec.DisplayName,
		AnnotationDigest: RequestDigest(spec),
	}
	record, err := u.volumes.CreateVolume(ctx, namespace, spec)
	if err == nil {
		return toVolume(record), nil
	}
	if !errors.Is(err, ErrConflict) {
		return Volume{}, err
	}
	// 查询与创建之间发生并发 AlreadyExists 时，按同一归属与摘要规则核对。
	existing, getErr := u.volumes.GetVolume(ctx, namespace, spec.Name)
	if getErr != nil {
		return Volume{}, getErr
	}
	return u.replayCreate(tenant, existing, spec)
}

func (u *UseCase) replayCreate(tenant string, existing *VolumeRecord, spec CreateSpec) (Volume, error) {
	if !owned(existing, tenant) || existing.Annotations[AnnotationDigest] != RequestDigest(spec) {
		return Volume{}, ErrCreateConflict
	}
	// 正在删除的同名对象仍返回其删除中状态，不恢复为新创建。
	return toVolume(existing), nil
}

// ListVolumes 按受管标签查询，筛选后稳定排序并分页；查询失败不返回空列表。
func (u *UseCase) ListVolumes(ctx context.Context, tenant string, page, pageSize uint32, filter VolumeFilter) ([]Volume, int64, error) {
	page, pageSize, offset, err := pageWindow(page, pageSize)
	if err != nil {
		return nil, 0, err
	}
	filter, err = NormalizeVolumeFilter(filter)
	if err != nil {
		return nil, 0, err
	}
	namespace, err := u.ensureNamespace(ctx, tenant)
	if err != nil {
		return nil, 0, err
	}
	records, err := u.volumes.ListVolumes(ctx, namespace, tenant)
	if err != nil {
		return nil, 0, err
	}
	filtered := make([]*VolumeRecord, 0, len(records))
	for _, record := range records {
		if owned(record, tenant) && matchVolumeFilter(record, filter) {
			filtered = append(filtered, record)
		}
	}
	sort.Slice(filtered, func(i, j int) bool { return filtered[i].Name < filtered[j].Name })
	total := int64(len(filtered))
	if offset >= total {
		return []Volume{}, total, nil
	}
	end := offset + int64(pageSize)
	if end > total {
		end = total
	}
	out := make([]Volume, 0, end-offset)
	for _, record := range filtered[offset:end] {
		out = append(out, toVolume(record))
	}
	return out, total, nil
}

// volumeDetailOperation marks the structured log records of the GetVolume RPC.
const volumeDetailOperation = "storage.volume_detail"

// C04 固定码：辅助组查询失败时 VolumeDetail 对应组的 error_code 取值。
const (
	VolumeEventsGroupUnavailableCode    = "VOLUME_EVENTS_UNAVAILABLE"
	VolumeConsumersGroupUnavailableCode = "VOLUME_CONSUMERS_UNAVAILABLE"
)

// volumeDetailAuxTimeout caps each auxiliary read group when the parent
// request carries no deadline.
const volumeDetailAuxTimeout = 2 * time.Second

// volumeDetailAuxContext derives one auxiliary group's child context from the
// parent once per request: with a remaining parent budget R each group gets
// min(2s, R/4), keeping a quarter for response encoding and outbound work. A
// parent that is already cancelled or expired returns its own error so no
// auxiliary read starts against a dead request.
func volumeDetailAuxContext(ctx context.Context) (context.Context, context.CancelFunc, error) {
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	budget := volumeDetailAuxTimeout
	if deadline, ok := ctx.Deadline(); ok {
		if quarter := time.Until(deadline) / 4; quarter < budget {
			budget = quarter
		}
	}
	call, cancel := context.WithTimeout(ctx, budget)
	return call, cancel, nil
}

// detailLogLevel demotes the caller's own cancellation/deadline to DEBUG;
// unexpected failures stay ERROR. Normal NotFound results are not logged.
func detailLogLevel(err error) slog.Level {
	switch {
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return slog.LevelDebug
	default:
		return slog.LevelError
	}
}

// GetVolume 返回 spec/status、conditions、事件、可观察使用方与回收策略展示。
// 主读取用父 ctx；consumers、events、reclaim 三组按 C05 顺序执行，共享请求级
// 一次性派生预算，组结束立即 cancel；任何一组失败只降级本组，不拖垮整个详情。
func (u *UseCase) GetVolume(ctx context.Context, tenant, name string) (VolumeDetail, error) {
	namespace, err := u.ensureNamespace(ctx, tenant)
	if err != nil {
		if !errors.Is(err, ErrNotFound) {
			logDeleteStep(u.logger, detailLogLevel(err), ctx, volumeDetailOperation, "storage volume detail namespace resolve failed",
				PVCIdentity{TenantID: tenant, ClusterID: u.cfg.ClusterID, PVCName: name}, err)
		}
		return VolumeDetail{}, err
	}
	id := PVCIdentity{TenantID: tenant, ClusterID: u.cfg.ClusterID, Namespace: namespace, PVCName: name}
	record, err := u.volumes.GetVolume(ctx, namespace, name)
	if err != nil {
		if !errors.Is(err, ErrNotFound) {
			logDeleteStep(u.logger, detailLogLevel(err), ctx, volumeDetailOperation, "storage volume detail main read failed", id, err)
		}
		return VolumeDetail{}, err
	}
	if !owned(record, tenant) {
		return VolumeDetail{}, ErrNotFound
	}
	detail := VolumeDetail{Volume: toVolume(record), Conditions: record.Conditions}

	call, cancel, auxErr := volumeDetailAuxContext(ctx)
	if auxErr != nil {
		logDeleteStep(u.logger, slog.LevelDebug, ctx, volumeDetailOperation, "storage volume detail interrupted by parent context", id, auxErr)
		return VolumeDetail{}, auxErr
	}
	consumers, usageErr := u.usage(call, namespace, name)
	cancel()
	if usageErr != nil {
		logDeleteStep(u.logger, slog.LevelWarn, ctx, volumeDetailOperation, "storage volume detail consumers group failed", id, usageErr)
		detail.Consumers, detail.ConsumersKnown, detail.ConsumersErrorCode = []Consumer{}, false, VolumeConsumersGroupUnavailableCode
	} else {
		if consumers == nil {
			consumers = []Consumer{}
		}
		detail.Consumers, detail.ConsumersKnown, detail.ConsumersErrorCode = consumers, true, ""
	}
	if err := ctx.Err(); err != nil {
		logDeleteStep(u.logger, slog.LevelDebug, ctx, volumeDetailOperation, "storage volume detail interrupted by parent context", id, err)
		return VolumeDetail{}, err
	}

	call, cancel, auxErr = volumeDetailAuxContext(ctx)
	if auxErr != nil {
		logDeleteStep(u.logger, slog.LevelDebug, ctx, volumeDetailOperation, "storage volume detail interrupted by parent context", id, auxErr)
		return VolumeDetail{}, auxErr
	}
	events, eventsErr := u.volumes.ListEvents(call, namespace, record.UID)
	cancel()
	if eventsErr != nil {
		logDeleteStep(u.logger, slog.LevelWarn, ctx, volumeDetailOperation, "storage volume detail events group failed", id, eventsErr)
		detail.Events, detail.EventsKnown, detail.EventsErrorCode = []Event{}, false, VolumeEventsGroupUnavailableCode
	} else {
		if events == nil {
			events = []Event{}
		}
		detail.Events, detail.EventsKnown, detail.EventsErrorCode = events, true, ""
	}
	if err := ctx.Err(); err != nil {
		logDeleteStep(u.logger, slog.LevelDebug, ctx, volumeDetailOperation, "storage volume detail interrupted by parent context", id, err)
		return VolumeDetail{}, err
	}

	call, cancel, auxErr = volumeDetailAuxContext(ctx)
	if auxErr != nil {
		logDeleteStep(u.logger, slog.LevelDebug, ctx, volumeDetailOperation, "storage volume detail interrupted by parent context", id, auxErr)
		return VolumeDetail{}, auxErr
	}
	detail.Volume.ReclaimPolicy, detail.Volume.ReclaimPolicyKnown = u.readReclaimPolicy(call, record)
	cancel()
	if err := ctx.Err(); err != nil {
		logDeleteStep(u.logger, slog.LevelDebug, ctx, volumeDetailOperation, "storage volume detail interrupted by parent context", id, err)
		return VolumeDetail{}, err
	}
	return detail, nil
}

// readReclaimPolicy 只读当前回收策略展示：优先已绑定 PV 的实际策略；无绑定
// PV 名时展示明确 StorageClass 的当前配置。查不到即为 unknown，不再 fallback
// 到其它来源，也不承诺未来 PV 的实际策略。
func (u *UseCase) readReclaimPolicy(ctx context.Context, record *VolumeRecord) (policy string, known bool) {
	if record.PVName != "" {
		pv, err := u.volumes.GetPersistentVolume(ctx, record.PVName)
		if err != nil {
			logDeleteStep(u.logger, slog.LevelWarn, ctx, volumeDetailOperation, "storage volume detail reclaim policy read failed",
				PVCIdentity{PVCName: record.Name}, err)
			return "", false
		}
		if pv != nil && pv.ReclaimPolicy != "" {
			return pv.ReclaimPolicy, true
		}
		return "", false
	}
	if record.StorageClassName != "" {
		class, err := u.volumes.GetStorageClass(ctx, record.StorageClassName)
		if err != nil {
			logDeleteStep(u.logger, slog.LevelWarn, ctx, volumeDetailOperation, "storage volume detail reclaim policy read failed",
				PVCIdentity{PVCName: record.Name}, err)
			return "", false
		}
		if class != nil && class.ReclaimPolicy != "" {
			return class.ReclaimPolicy, true
		}
	}
	return "", false
}

// UpdateVolume 只允许扩容，且以 spec 当前申请值为比较基准。
func (u *UseCase) UpdateVolume(ctx context.Context, tenant, name, expectedUID string, capacityGB int64) (Volume, error) {
	bytes, err := CapacityBytes(capacityGB)
	if err != nil {
		return Volume{}, err
	}
	ns, err := u.namespaceIdentity(ctx, tenant)
	if err != nil {
		return Volume{}, err
	}
	namespace := ns.Name
	record, err := u.volumes.GetVolume(ctx, namespace, name)
	if err != nil {
		return Volume{}, err
	}
	if !owned(record, tenant) {
		return Volume{}, ErrNotFound
	}
	if record.UID != expectedUID {
		return Volume{}, ErrIdentityChanged
	}
	if record.Deleting {
		return Volume{}, ErrVolumeTerminating
	}
	switch {
	case bytes == record.RequestedBytes:
		return toVolume(record), nil
	case bytes < record.RequestedBytes:
		return Volume{}, ErrCapacityDecrease
	}
	class, err := u.volumes.GetStorageClass(ctx, record.StorageClassName)
	if err != nil {
		if errors.Is(err, ErrNotFound) {
			return Volume{}, ErrExpansionDenied
		}
		return Volume{}, err
	}
	if !class.AllowVolumeExpansion {
		return Volume{}, ErrExpansionDenied
	}
	if err := u.verifyNamespaceUID(ctx, tenant, ns.UID); err != nil {
		return Volume{}, err
	}
	updated, err := u.volumes.ResizeVolume(ctx, namespace, name, expectedUID, record.ResourceVersion, strconv.FormatInt(capacityGB, 10)+"G", bytes)
	if err != nil {
		return Volume{}, err
	}
	return toVolume(updated), nil
}

// DeleteVolume reserves the durable name barrier before any Kubernetes DELETE.
func (u *UseCase) DeleteVolume(ctx context.Context, tenant, name, expectedUID string) (DeleteResult, error) {
	return u.deleteVolumeGuarded(ctx, tenant, name, expectedUID)
}

// usage 只统计引用目标 PVC 的 Pod 直接引用与 Notebook 模板引用；
// 同命名空间内引用其它 PVC 的工作负载不算使用方。
func (u *UseCase) usage(ctx context.Context, namespace, claimName string) ([]Consumer, error) {
	pods, err := u.volumes.ListPodConsumers(ctx, namespace, claimName)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUsageUnknown, err)
	}
	notebooks, err := u.volumes.ListNotebookConsumers(ctx, namespace, claimName)
	if err != nil && !errors.Is(err, ErrNotInstalled) {
		return nil, fmt.Errorf("%w: %v", ErrUsageUnknown, err)
	}
	out := make([]Consumer, 0, len(pods)+len(notebooks))
	out = append(out, pods...)
	out = append(out, notebooks...)
	sort.Slice(out, func(i, j int) bool {
		if out[i].Kind != out[j].Kind {
			return out[i].Kind < out[j].Kind
		}
		return out[i].Name < out[j].Name
	})
	return out, nil
}

// ListBuckets 返回配置给当前租户的桶；启用租户建桶时合并 S3 中由本服务创建的桶。
func (u *UseCase) ListBuckets(ctx context.Context, tenant string, filter BucketFilter) ([]Bucket, error) {
	if !CanonicalUUID(tenant) {
		return nil, ErrInvalid
	}
	filter, err := NormalizeBucketFilter(filter)
	if err != nil {
		return nil, err
	}
	out := []Bucket{}
	configured := map[string]bool{}
	logical := map[string]string{}
	for _, b := range u.cfg.Buckets {
		configured[b.Bucket] = true
		if b.TenantID != tenant {
			continue
		}
		logical[b.ID] = b.Bucket
		bucket := Bucket{ID: b.ID, Name: b.Name, Region: u.cfg.Region, StorageClass: StorageClassStandard, ACL: ACLPrivate}
		if matchBucketFilter(bucket, filter) {
			out = append(out, bucket)
		}
	}
	if u.cfg.TenantBucketPrefix == "" {
		sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
		return out, nil
	}
	if u.objects == nil {
		return nil, ErrBackendUnavailable
	}
	infos, err := u.objects.ListBuckets(ctx)
	if err != nil {
		return nil, err
	}
	prefix, err := tenantBucketPrefix(u.cfg.TenantBucketPrefix, u.cfg.ClusterID, tenant)
	if err != nil {
		return nil, err
	}
	for _, info := range infos {
		name := info.Name
		if !strings.HasPrefix(name, prefix) || configured[name] {
			continue
		}
		id := strings.TrimPrefix(name, prefix)
		if ValidateBucketID(id) != nil {
			continue
		}
		if previous, exists := logical[id]; exists && previous != name {
			return nil, ErrConflict
		}
		logical[id] = name
		bucket := Bucket{ID: id, Name: id, CreatedAt: info.CreatedAt, Region: u.cfg.Region, StorageClass: StorageClassStandard, ACL: ACLPrivate}
		if matchBucketFilter(bucket, filter) {
			out = append(out, bucket)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, nil
}

// CreateBucket 在租户前缀下创建物理桶；同名重复创建幂等返回同一个桶。
func (u *UseCase) CreateBucket(ctx context.Context, tenant, name string) (Bucket, error) {
	if !CanonicalUUID(tenant) {
		return Bucket{}, ErrInvalid
	}
	if u.cfg.TenantBucketPrefix == "" {
		return Bucket{}, ErrBucketDisabled
	}
	if err := ValidateBucketID(name); err != nil {
		return Bucket{}, err
	}
	for _, b := range u.cfg.Buckets {
		if b.TenantID == tenant && b.ID == name {
			return Bucket{}, ErrBucketExists
		}
	}
	physical, err := TenantBucketName(u.cfg.TenantBucketPrefix, u.cfg.ClusterID, tenant, name)
	if err != nil {
		return Bucket{}, err
	}
	if u.objects == nil {
		return Bucket{}, ErrBackendUnavailable
	}
	infos, err := u.objects.ListBuckets(ctx)
	if err != nil {
		return Bucket{}, err
	}
	prefix, err := tenantBucketPrefix(u.cfg.TenantBucketPrefix, u.cfg.ClusterID, tenant)
	if err != nil {
		return Bucket{}, err
	}
	configured := map[string]bool{}
	for _, b := range u.cfg.Buckets {
		configured[b.Bucket] = true
		if b.Bucket == physical {
			return Bucket{}, ErrBucketExists
		}
	}
	count := 0
	for _, info := range infos {
		if info.Name == physical {
			return Bucket{ID: name, Name: name, CreatedAt: info.CreatedAt, Region: u.cfg.Region, StorageClass: StorageClassStandard, ACL: ACLPrivate}, nil
		}
		if strings.HasPrefix(info.Name, prefix) && !configured[info.Name] {
			count++
		}
	}
	if count >= u.cfg.MaxBucketsPerTenant {
		return Bucket{}, ErrBucketLimit
	}
	if err := u.objects.CreateBucket(ctx, physical); err != nil {
		return Bucket{}, err
	}
	return Bucket{ID: name, Name: name, Region: u.cfg.Region, StorageClass: StorageClassStandard, ACL: ACLPrivate}, nil
}

// BucketStats 按需统计桶内对象数量与字节数；超过扫描上限时 truncated=true。
func (u *UseCase) BucketStats(ctx context.Context, tenant, bucketID string) (ObjectStats, error) {
	bucket, err := u.bucket(tenant, bucketID)
	if err != nil {
		return ObjectStats{}, err
	}
	if u.objects == nil {
		return ObjectStats{}, ErrBackendUnavailable
	}
	if err := u.objects.HeadBucket(ctx, bucket.Bucket); err != nil {
		return ObjectStats{}, err
	}
	return u.objects.BucketStats(ctx, bucket.Bucket, maxBucketStatsScan)
}

// UploadURL 签发绑定单个 key 的短期 PUT URL；服务端不承载文件字节。
func (u *UseCase) UploadURL(ctx context.Context, tenant, bucketID, key, contentType string, size int64, overwrite bool) (UploadTicket, error) {
	if u.objects == nil || !u.objects.Available() {
		return UploadTicket{}, ErrUploadUnavailable
	}
	bucket, err := u.bucket(tenant, bucketID)
	if err != nil {
		return UploadTicket{}, err
	}
	if err := ValidateUploadKey(key); err != nil {
		return UploadTicket{}, err
	}
	if err := ValidateUploadSize(size, u.cfg.MaxUploadBytes); err != nil {
		return UploadTicket{}, err
	}
	contentType, err = NormalizeContentType(contentType)
	if err != nil {
		return UploadTicket{}, err
	}
	if err := u.objects.HeadBucket(ctx, bucket.Bucket); err != nil {
		return UploadTicket{}, err
	}
	if !overwrite {
		_, err := u.objects.HeadObject(ctx, bucket.Bucket, key)
		if err == nil {
			return UploadTicket{}, ErrObjectExists
		}
		if !errors.Is(err, ErrNotFound) {
			return UploadTicket{}, err
		}
	}
	url, headers, expires, err := u.objects.PresignPutObject(ctx, bucket.Bucket, key, contentType, overwrite, u.cfg.UploadTTL)
	if err != nil {
		return UploadTicket{}, err
	}
	return UploadTicket{URL: url, Headers: headers, ExpiresAt: expires, Key: key}, nil
}

// DeleteObject 删除单个对象；不存在返回 GONE，保持幂等。
func (u *UseCase) DeleteObject(ctx context.Context, tenant, bucketID, key, expectedETag string) (string, error) {
	bucket, err := u.bucket(tenant, bucketID)
	if err != nil {
		return "", err
	}
	if err := ValidateObjectKey(key); err != nil {
		return "", err
	}
	if u.objects == nil {
		return "", ErrBackendUnavailable
	}
	if len(expectedETag) > 256 || strings.ContainsAny(expectedETag, "\r\n\x00") || (expectedETag != "" && NormalizeETag(expectedETag) == "") {
		return "", ErrInvalid
	}
	if err := u.objects.HeadBucket(ctx, bucket.Bucket); err != nil {
		return "", err
	}
	meta, err := u.objects.HeadObject(ctx, bucket.Bucket, key)
	if errors.Is(err, ErrNotFound) {
		return ObjectGone, nil
	}
	if err != nil {
		return "", err
	}
	if expectedETag != "" && NormalizeETag(expectedETag) != NormalizeETag(meta.ETag) {
		return "", ErrObjectIdentity
	}
	if err := u.objects.DeleteObject(ctx, bucket.Bucket, key, expectedETag); err != nil {
		if errors.Is(err, ErrNotFound) {
			return ObjectGone, nil
		}
		return "", err
	}
	return ObjectDeleted, nil
}

// ListObjects 使用领域游标；limit 缺省 100，显式 0 或越界一律 400。
func (u *UseCase) ListObjects(ctx context.Context, tenant, bucketID, prefix string, limitSet bool, limit int, cursor string) (ObjectPage, error) {
	bucket, err := u.bucket(tenant, bucketID)
	if err != nil {
		return ObjectPage{}, err
	}
	if err := validatePrefix(prefix); err != nil {
		return ObjectPage{}, err
	}
	if limitSet {
		if limit < 1 || limit > maximumLimit {
			return ObjectPage{}, ErrInvalid
		}
	} else {
		limit = defaultLimit
	}
	token, err := DecodeCursor(cursor, bucketID, prefix)
	if err != nil {
		return ObjectPage{}, err
	}
	if u.objects == nil {
		return ObjectPage{}, ErrBackendUnavailable
	}
	page, err := u.objects.ListObjects(ctx, bucket.Bucket, prefix, token, int32(limit))
	if err != nil {
		return ObjectPage{}, err
	}
	if page.IsTruncated && page.NextToken != "" {
		page.NextToken = EncodeCursor(bucketID, prefix, page.NextToken)
	} else {
		page.NextToken = ""
	}
	return page, nil
}

// HeadObject 返回真实 HEAD 元数据；key 原样交给 SDK。
func (u *UseCase) HeadObject(ctx context.Context, tenant, bucketID, key string) (ObjectMeta, error) {
	bucket, err := u.bucket(tenant, bucketID)
	if err != nil {
		return ObjectMeta{}, err
	}
	if err := ValidateObjectKey(key); err != nil {
		return ObjectMeta{}, err
	}
	if u.objects == nil {
		return ObjectMeta{}, ErrBackendUnavailable
	}
	return u.objects.HeadObject(ctx, bucket.Bucket, key)
}

// DownloadURL 用后端凭据对浏览器 endpoint 生成固定对象的短期 GET URL。
func (u *UseCase) DownloadURL(ctx context.Context, tenant, bucketID, key string) (string, time.Time, error) {
	bucket, err := u.bucket(tenant, bucketID)
	if err != nil {
		return "", time.Time{}, err
	}
	if err := ValidateObjectKey(key); err != nil {
		return "", time.Time{}, err
	}
	if u.objects == nil || !u.objects.Available() {
		return "", time.Time{}, ErrDownloadUnavailable
	}
	return u.objects.PresignGetObject(ctx, bucket.Bucket, key, u.cfg.DownloadTTL)
}

func (u *UseCase) bucket(tenant, id string) (BucketConfig, error) {
	if !CanonicalUUID(tenant) || id == "" {
		return BucketConfig{}, ErrInvalid
	}
	for _, b := range u.cfg.Buckets {
		if b.ID == id && b.TenantID == tenant {
			return b, nil
		}
	}
	if u.cfg.TenantBucketPrefix != "" && ValidateBucketID(id) == nil {
		if name, err := TenantBucketName(u.cfg.TenantBucketPrefix, u.cfg.ClusterID, tenant, id); err == nil {
			for _, b := range u.cfg.Buckets {
				if b.Bucket == name {
					return BucketConfig{}, ErrNotFound
				}
			}
			return BucketConfig{ID: id, TenantID: tenant, Name: id, Bucket: name}, nil
		}
	}
	return BucketConfig{}, ErrNotFound
}

func pageWindow(page, size uint32) (uint32, uint32, int64, error) {
	if page == 0 {
		page = 1
	}
	if size == 0 {
		size = defaultPageSize
	}
	if size > maximumPageSize {
		return 0, 0, 0, ErrInvalid
	}
	return page, size, int64(page-1) * int64(size), nil
}

func toVolume(record *VolumeRecord) Volume {
	gb := record.RequestedBytes / BytesPerGB
	if record.RequestedBytes > 0 && record.RequestedBytes%BytesPerGB != 0 {
		gb = GBFromBytes(record.RequestedBytes)
	}
	return Volume{
		Name:                  record.Name,
		UID:                   record.UID,
		DisplayName:           record.DisplayName,
		StorageClassName:      record.StorageClassName,
		VolumeMode:            record.VolumeMode,
		AccessModes:           append([]string(nil), record.AccessModes...),
		RequestedCapacityGB:   gb,
		ObservedCapacityBytes: record.ObservedBytes,
		ObservedCapacityKnown: record.ObservedKnown,
		ObservedQuantity:      record.ObservedQuantity,
		Phase:                 record.Phase,
		Deleting:              record.Deleting,
	}
}
