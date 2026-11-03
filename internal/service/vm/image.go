package vm

import (
	"context"
	"errors"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/vm/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/fieldmaskpb"
)

// ImageUploadConfig is the service-facing projection of the deployment block.
// Every field is fixed by configuration; none may arrive from a request.
// ImageUploadConfig mirrors the deployment block written in ANI_VM_CONFIG_FILE.
// Durations stay strings here so the JSON keeps the same human form the rest of
// the file uses instead of nanosecond integers.
type ImageUploadConfig struct {
	Enabled              bool
	ExternalUploadOrigin string
	ValidatorImage       string
	UploadDeadline       string
	TokenTTL             string
	MaxFileBytes         int64
	MaxSourceGB          int64
}

// Durations resolves the two deadline inputs with the documented defaults.
func (c ImageUploadConfig) Durations() (time.Duration, time.Duration, error) {
	deadline, e := time.ParseDuration(c.UploadDeadline)
	if c.UploadDeadline == "" {
		deadline, e = 24*time.Hour, nil
	}
	if e != nil || deadline <= 0 {
		return 0, 0, biz.ErrImageInvalid
	}
	ttl, e := time.ParseDuration(c.TokenTTL)
	if c.TokenTTL == "" {
		ttl, e = 5*time.Minute, nil
	}
	if e != nil || ttl <= 0 || ttl >= deadline {
		return 0, 0, biz.ErrImageInvalid
	}
	return deadline, ttl, nil
}

// ImageService is the trusted governance adapter for the image catalog.
type ImageService struct {
	pb.UnimplementedVMImageServiceServer
	repo        biz.ImageRepository
	native      biz.ImageNativePort
	environment biz.EnvironmentPort
	upload      ImageUploadConfig
	tokenTTL    time.Duration
	cluster     string
	logger      *slog.Logger
}

func NewImageService(repo biz.ImageRepository, native biz.ImageNativePort, environment biz.EnvironmentPort, upload ImageUploadConfig, cluster string, logger *slog.Logger) (*ImageService, error) {
	if repo == nil || native == nil || environment == nil || cluster == "" || logger == nil {
		return nil, biz.ErrImageInvalid
	}
	if upload.Enabled && (!strings.HasPrefix(upload.ExternalUploadOrigin, "https://") || !dataImageNative(upload.ValidatorImage)) {
		return nil, biz.ErrImageInvalid
	}
	deadline, ttl, e := upload.Durations()
	if e != nil {
		return nil, e
	}
	upload.UploadDeadline, upload.TokenTTL = deadline.String(), ttl.String()
	return &ImageService{repo: repo, native: native, environment: environment, upload: upload, tokenTTL: ttl, cluster: cluster, logger: logger}, nil
}

func dataImageNative(image string) bool {
	at := strings.LastIndex(image, "@")
	return at > 0 && strings.HasPrefix(image[at+1:], "sha256:") && len(image)-at == 71
}

// imageRPCError keeps one stable reason per business failure and never exposes
// a Kubernetes status string or a native object identity.
func imageRPCError(e error) error {
	if e == nil {
		return nil
	}
	switch {
	case errors.Is(e, biz.ErrImageInvalid):
		return status.Error(codes.InvalidArgument, e.Error())
	case errors.Is(e, biz.ErrImageNotFound), errors.Is(e, biz.ErrNotFound):
		return status.Error(codes.NotFound, biz.ErrImageNotFound.Error())
	case errors.Is(e, biz.ErrDenied):
		return status.Error(codes.PermissionDenied, e.Error())
	case errors.Is(e, biz.ErrImageIdempotency):
		return status.Error(codes.AlreadyExists, e.Error())
	case errors.Is(e, biz.ErrImageVersion), errors.Is(e, biz.ErrVersion):
		return status.Error(codes.FailedPrecondition, e.Error())
	case errors.Is(e, biz.ErrImageInUse), errors.Is(e, biz.ErrBusy):
		return status.Error(codes.FailedPrecondition, biz.ErrImageInUse.Error())
	case errors.Is(e, biz.ErrImageNotReady), errors.Is(e, biz.ErrImageNotUploadable), errors.Is(e, biz.ErrImageKindMismatch), errors.Is(e, biz.ErrImageIdentity):
		return status.Error(codes.FailedPrecondition, e.Error())
	case errors.Is(e, biz.ErrImageUploadOff):
		// The capability is switched off, not the dependency: a 503 here would be
		// indistinguishable from Resource being unreachable, for the BFF and the operator.
		return status.Error(codes.FailedPrecondition, e.Error())
	case errors.Is(e, biz.ErrImageDependency), errors.Is(e, biz.ErrDependency), errors.Is(e, biz.ErrChanged), errors.Is(e, biz.ErrOwnership):
		return status.Error(codes.Unavailable, biz.ErrImageDependency.Error())
	}
	return status.Error(codes.Unavailable, biz.ErrImageDependency.Error())
}

func (s *ImageService) enabled() bool { return s.upload.Enabled }

// ValidateStaticMediaKeys fails startup when the static catalog already uses the
// reserved image prefix, and reports the offending key instead of renaming it.
func ValidateStaticMediaKeys(media []*pb.MediaReference) error {
	for _, item := range media {
		if item == nil {
			return biz.ErrImageInvalid
		}
		if strings.HasPrefix(item.MediaKey, biz.ImageMediaKeyPrefix) {
			return biz.ImageCatalogConflict(item.MediaKey)
		}
	}
	return nil
}

func wireImage(i biz.Image) *pb.VMImage {
	return &pb.VMImage{ImageId: i.ID, MediaKey: i.MediaKey, Name: i.Name, Description: i.Description,
		Kind: i.Kind, Format: i.Format, State: i.State, Reason: i.Reason, CleanupState: i.CleanupState,
		SourceCapacityGb: i.SourceCapacityGB, SourceRequestedBytes: i.SourceRequestedBytes,
		ObservedSourceCapacityBytes: i.ObservedSourceCapacityBytes, SourceChecksumBytes: i.SourceChecksumBytes,
		SourceSha256: i.SourceSHA256, Version: i.Version, CreatedBy: i.CreatedBy,
		CreatedAt: i.CreatedAt.UTC().Format(time.RFC3339Nano), UpdatedAt: i.UpdatedAt.UTC().Format(time.RFC3339Nano),
		Os: &pb.ImageOS{OsFamily: i.OS.OSFamily, OsVersion: i.OS.OSVersion, Architecture: i.OS.Architecture,
			Firmware: i.OS.Firmware, RootBus: i.OS.RootBus, DataBus: i.OS.DataBus, NicModel: i.OS.NICModel}}
}

func wireImageOS(in *pb.ImageOS) (biz.ImageOS, error) {
	if in == nil {
		return biz.ImageOS{}, biz.ErrImageInvalid
	}
	return biz.ImageOSProfile(in.OsFamily, in.OsVersion, in.Architecture)
}

func (s *ImageService) ListImages(ctx context.Context, r *pb.ListImagesRequest) (*pb.ListImagesResponse, error) {
	if r == nil {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, imageRPCError(e)
	}
	items, total, e := s.repo.ListImages(ctx, r.TenantId, biz.ImageListFilter{Page: r.Page, PageSize: r.PageSize, Kind: r.Kind, State: r.State, Keyword: r.Keyword})
	if e != nil {
		return nil, imageRPCError(e)
	}
	out := &pb.ListImagesResponse{Total: total}
	for _, item := range items {
		out.Items = append(out.Items, wireImage(item))
	}
	if out.Items == nil {
		out.Items = []*pb.VMImage{}
	}
	return out, nil
}

func (s *ImageService) GetImage(ctx context.Context, r *pb.GetImageRequest) (*pb.VMImage, error) {
	if r == nil {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, imageRPCError(e)
	}
	i, e := s.repo.FindImage(ctx, r.TenantId, r.ImageId)
	if e != nil {
		return nil, imageRPCError(e)
	}
	return wireImage(i), nil
}

// CreateImage is idempotent and returns the full record. A replay wins before
// any state or version check so a retry never forks a second image.
func (s *ImageService) CreateImage(ctx context.Context, r *pb.CreateImageRequest) (*pb.VMImage, error) {
	if r == nil || r.IdempotencyKey == "" || len(r.IdempotencyKey) > 256 {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, imageRPCError(e)
	}
	if !s.enabled() {
		return nil, imageRPCError(biz.ErrImageUploadOff)
	}
	format, ok := biz.ImageFormatFor(r.Kind)
	if !ok || r.Format != format {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if r.SourceCapacityGb < 1 || s.upload.MaxSourceGB > 0 && r.SourceCapacityGb > s.upload.MaxSourceGB {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	profile, e := wireImageOS(r.Os)
	if e != nil {
		return nil, imageRPCError(e)
	}
	id := uuid.NewString()
	digest, e := biz.ImageRequestDigest(biz.ImageCreateAction, r.TenantId, id, struct {
		Name, Description, Kind, Format string
		Size                            int64
		OS                              biz.ImageOS
	}{strings.TrimSpace(r.Name), r.Description, r.Kind, r.Format, r.SourceCapacityGb, profile}, 0, nil)
	if e != nil {
		return nil, imageRPCError(e)
	}
	command := biz.ImageCommand{TenantID: r.TenantId, ImageID: id, Action: biz.ImageCreateAction, Key: r.IdempotencyKey, RequestDigest: digest}
	desired, e := biz.Image{TenantID: r.TenantId, ID: id, Name: strings.TrimSpace(r.Name), Description: r.Description,
		Kind: r.Kind, Format: r.Format, SourceCapacityGB: r.SourceCapacityGb, OS: profile,
		CreatedBy: biz.CallerFrom(ctx).Actor}.Validated()
	if e != nil {
		return nil, imageRPCError(e)
	}
	i, e := s.repo.CreateImage(ctx, desired, command)
	if e != nil {
		return nil, imageRPCError(e)
	}
	return wireImage(i), nil
}

// CreateUploadSession issues a short-lived ticket for one still-uploadable
// target. The token is returned once and never stored anywhere.
func (s *ImageService) CreateUploadSession(ctx context.Context, r *pb.CreateUploadSessionRequest) (*pb.UploadSession, error) {
	if r == nil || r.IdempotencyKey == "" || len(r.IdempotencyKey) > 256 || !biz.UUID(r.ImageId) {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, imageRPCError(e)
	}
	if !s.enabled() {
		return nil, imageRPCError(biz.ErrImageUploadOff)
	}
	digest, e := biz.ImageRequestDigest(biz.ImageSessionAction, r.TenantId, r.ImageId, struct{}{}, 0, nil)
	if e != nil {
		return nil, imageRPCError(e)
	}
	command := biz.ImageCommand{TenantID: r.TenantId, ImageID: r.ImageId, Action: biz.ImageSessionAction, Key: r.IdempotencyKey, RequestDigest: digest}
	session := uuid.NewString()
	i, e := s.repo.RecordSession(ctx, command, session, time.Now().UTC().Add(s.tokenTTL))
	if e != nil {
		return nil, imageRPCError(e)
	}
	if i.Native.DVUID == "" {
		// The target is registered but not provisioned yet: the caller polls the
		// record instead of the client guessing a native name.
		return nil, imageRPCError(biz.ErrImageNotReady)
	}
	updated, token, expires, e := s.native.CreateUploadToken(ctx, i, session, s.tokenTTL)
	if e != nil {
		return nil, imageRPCError(e)
	}
	if e = s.repo.ConfirmImageSession(ctx, updated); e != nil {
		return nil, imageRPCError(e)
	}
	s.logger.Info("VM image upload session issued", "image_id", i.ID, "stage", "upload_ticket", "expires_at", expires.UTC().Format(time.RFC3339Nano))
	return &pb.UploadSession{ImageId: i.ID, UploadSessionId: session, Method: "PUT", UploadUrl: s.uploadURL(),
		BearerToken: token, ExpiresAt: expires.UTC().Format(time.RFC3339Nano), ContentType: "application/octet-stream",
		MaxFileBytes: s.upload.MaxFileBytes}, nil
}

func (s *ImageService) uploadURL() string {
	return strings.TrimRight(s.upload.ExternalUploadOrigin, "/") + "/v1beta1/upload-async"
}

// UpdateImage accepts only name and description through the field mask.
func (s *ImageService) UpdateImage(ctx context.Context, r *pb.UpdateImageRequest) (*pb.VMImage, error) {
	if r == nil || !biz.UUID(r.ImageId) || r.IdempotencyKey == "" || len(r.IdempotencyKey) > 256 || r.ExpectedVersion < 1 {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, imageRPCError(e)
	}
	mask, e := imageUpdateMask(r.UpdateMask)
	if e != nil {
		return nil, imageRPCError(e)
	}
	name, description := r.Name, r.Description
	digest, e := biz.ImageRequestDigest(biz.ImageUpdateAction, r.TenantId, r.ImageId, struct {
		Name, Description string
	}{name, description}, r.ExpectedVersion, mask)
	if e != nil {
		return nil, imageRPCError(e)
	}
	command := biz.ImageCommand{TenantID: r.TenantId, ImageID: r.ImageId, Action: biz.ImageUpdateAction,
		Key: r.IdempotencyKey, RequestDigest: digest, ExpectedVersion: r.ExpectedVersion, Mask: mask}
	i, e := s.repo.UpdateImage(ctx, command, name, description, mask)
	if e != nil {
		return nil, imageRPCError(e)
	}
	return wireImage(i), nil
}

// imageUpdateMask rejects empty, wildcard, unknown and repeated paths, and
// preserves the difference between an absent field and an explicit clear.
func imageUpdateMask(mask *fieldmaskpb.FieldMask) ([]string, error) {
	if mask == nil || len(mask.Paths) == 0 {
		return nil, biz.ErrImageInvalid
	}
	seen := map[string]bool{}
	out := make([]string, 0, len(mask.Paths))
	for _, path := range mask.Paths {
		path = strings.TrimSpace(path)
		if path == "" || path == "*" || path != "name" && path != "description" || seen[path] {
			return nil, biz.ErrImageInvalid
		}
		seen[path] = true
		out = append(out, path)
	}
	return out, nil
}

func (s *ImageService) DeleteImage(ctx context.Context, r *pb.DeleteImageRequest) (*pb.VMImage, error) {
	if r == nil || !biz.UUID(r.ImageId) || r.IdempotencyKey == "" || len(r.IdempotencyKey) > 256 || r.ExpectedVersion < 1 {
		return nil, imageRPCError(biz.ErrImageInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, imageRPCError(e)
	}
	// A delete replays on the stored row even after the upload switch is off, so
	// the gate deliberately does not apply here.
	digest, e := biz.ImageRequestDigest(biz.ImageDeleteAction, r.TenantId, r.ImageId, struct{}{}, r.ExpectedVersion, nil)
	if e != nil {
		return nil, imageRPCError(e)
	}
	i, e := s.repo.DeleteImage(ctx, biz.ImageCommand{TenantID: r.TenantId, ImageID: r.ImageId, Action: biz.ImageDeleteAction,
		Key: r.IdempotencyKey, RequestDigest: digest, ExpectedVersion: r.ExpectedVersion})
	if e != nil {
		return nil, imageRPCError(e)
	}
	return wireImage(i), nil
}

var _ pb.VMImageServiceServer = (*ImageService)(nil)
