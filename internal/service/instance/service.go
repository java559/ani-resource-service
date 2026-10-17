package instance

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"google.golang.org/protobuf/types/known/timestamppb"
	"strings"
	"sync"

	"github.com/google/uuid"
	attachment "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/integration/v1"
	acc "github.com/zhangzhe-ctrl/ani-accelerator-service/api/gen/go/accelerator/v1"
	terminal "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/terminal/v1"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/v1"
	imagebiz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/image"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

type Config struct {
	NamespacePrefix, ClusterID, ServiceAccount, StorageClass, WorkspaceSize, Shell string
	GPURuntimeClassName                                                            string
	LogLimits                                                                      biz.LogLimits
	UID, GID                                                                       int64
}
type Service struct {
	pb.UnimplementedInstanceServiceServer
	repo                     biz.Repository
	presets                  biz.PresetRepository
	diagnostics              biz.Diagnostics
	logMu                    sync.Mutex
	logTotal                 int
	logTenants, logInstances map[string]int
	images                   biz.ImageResolver
	environment              biz.EnvironmentValidator
	config                   Config
}

func NewService(r biz.Repository, images biz.ImageResolver, environment biz.EnvironmentValidator, c Config) (*Service, error) {
	// An empty supply catalog is a valid closed experiment. Query and recovery
	// stay available; new creates still require a configured, frozen preset.
	if biz.ValidateLogLimits(c.LogLimits) != nil {
		return nil, biz.ErrInvalid
	}
	if r == nil || images == nil || environment == nil || !biz.UUID(c.ClusterID) || c.ServiceAccount == "" || c.StorageClass == "" || c.WorkspaceSize == "" || c.Shell == "" || c.UID < 0 || c.GID < 0 {
		return nil, biz.ErrInvalid
	}
	if c.GPURuntimeClassName != "" && pb.ValidateGPURuntimeClassName(c.GPURuntimeClassName) != nil {
		return nil, biz.ErrInvalid
	}
	presets, ok := r.(biz.PresetRepository)
	if !ok {
		return nil, biz.ErrInvalid
	}
	diagnostics, _ := environment.(biz.Diagnostics)
	return &Service{repo: r, presets: presets, images: images, environment: environment, diagnostics: diagnostics, config: c, logTenants: map[string]int{}, logInstances: map[string]int{}}, nil
}
func rpcError(e error) error {
	if e == nil {
		return nil
	}
	switch {
	case errors.Is(e, pb.ErrInvalidScheduling):
		return status.Error(codes.InvalidArgument, "INVALID_SCHEDULING_OPTIONS")
	case errors.Is(e, pb.ErrSchedulingConflict), errors.Is(e, biz.ErrSchedulingConflict):
		return status.Error(codes.FailedPrecondition, "SCHEDULING_FIELD_CONFLICT")
	case errors.Is(e, biz.ErrPresetConflict):
		return status.Error(codes.AlreadyExists, "COMPUTE_PRESET_CODE_CONFLICT")
	case errors.Is(e, biz.ErrPresetDisabled):
		return status.Error(codes.FailedPrecondition, "COMPUTE_PRESET_DISABLED")
	case errors.Is(e, biz.ErrOwnership):
		return status.Error(codes.FailedPrecondition, "INSTANCE_OBJECT_OWNERSHIP_CONFLICT")
	case errors.Is(e, biz.ErrDiagnosticTarget), errors.Is(e, biz.ErrPodChanged):
		return status.Error(codes.Unavailable, "INSTANCE_DIAGNOSTIC_TARGET_UNAVAILABLE")
	case errors.Is(e, biz.ErrLogLimit), errors.Is(e, biz.ErrLogSlow):
		return status.Error(codes.ResourceExhausted, e.Error())
	case errors.Is(e, context.Canceled), errors.Is(e, context.DeadlineExceeded):
		return status.FromContextError(e).Err()
	case errors.Is(e, biz.ErrDenied):
		return status.Error(codes.PermissionDenied, "INSTANCE_SCOPE_DENIED")
	case errors.Is(e, biz.ErrInvalid), errors.Is(e, pb.ErrInvalidBusiness):
		return status.Error(codes.InvalidArgument, "INVALID_INSTANCE_REQUEST")
	case errors.Is(e, biz.ErrNotFound):
		return status.Error(codes.NotFound, "INSTANCE_NOT_FOUND")
	case errors.Is(e, biz.ErrConflict):
		return status.Error(codes.AlreadyExists, "INSTANCE_IDEMPOTENCY_CONFLICT")
	case errors.Is(e, biz.ErrVersion):
		return status.Error(codes.FailedPrecondition, "INSTANCE_VERSION_CONFLICT")
	default:
		return status.Error(codes.Unavailable, "INSTANCE_DEPENDENCY_UNAVAILABLE")
	}
}
func (s *Service) ResolveCreateSpec(ctx context.Context, r *pb.ResolveCreateSpecRequest) (*pb.CreateBusiness, error) {
	if r == nil || r.Data == nil || strings.TrimSpace(r.Data.Name) == "" || len([]rune(r.Data.Name)) > 80 || (r.Data.ImageScope != "tenant" && r.Data.ImageScope != "platform") || !biz.UUID(r.Data.PresetId) {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	if _, e := imagebiz.ParseImageID(r.Data.ImageId); e != nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	scheduling, e := pb.ResolveSchedulingOptions(r.Data.Scheduling, r.SchedulingDefaults)
	if e != nil {
		return nil, rpcError(e)
	}
	preset, e := s.presets.GetPreset(ctx, r.Data.PresetId)
	if e != nil {
		return nil, rpcError(e)
	}
	if !preset.Enabled {
		return nil, rpcError(biz.ErrPresetDisabled)
	}
	if e = biz.ValidatePreset(preset); e != nil {
		return nil, rpcError(e)
	}
	if preset.GPU != nil && preset.GPU.ClusterID != s.config.ClusterID {
		return nil, rpcError(biz.ErrInvalid)
	}
	ns, e := biz.ResolveNamespace(r.TenantId, r.Namespace, s.config.NamespacePrefix)
	if e != nil {
		return nil, rpcError(e)
	}
	if e = s.environment.ValidateEnvironment(ctx, r.TenantId, ns, s.config.ServiceAccount, s.config.StorageClass); e != nil {
		return nil, rpcError(e)
	}
	image, e := s.images.Resolve(ctx, r.TenantId, r.Data.ImageId, r.Data.ImageScope)
	if e != nil {
		return nil, rpcError(e)
	}
	c := s.config
	var gpu *acc.GpuRequest
	var gpuSpecDigest *string
	if g := preset.GPU; g != nil {
		value := g.SpecDigest
		gpuSpecDigest = &value
		gpu = &acc.GpuRequest{ClusterId: g.ClusterID, PoolId: g.PoolID, ProfileId: g.ProfileID, ProfileVersion: g.ProfileVersion, Replicas: 1, DevicesPerReplica: g.DevicesPerInstance, ContainerName: biz.Container}
	}
	out := &pb.CreateBusiness{Data: proto.Clone(r.Data).(*pb.CreateInstanceData), Gpu: gpu, Spec: &pb.FrozenSpec{InstanceKind: biz.KindContainer, ComputeType: preset.ComputeType, ImageReference: image.Reference, ImageDigest: image.Digest, Cpu: fmt.Sprintf("%dm", preset.CPUMillis), Memory: fmt.Sprintf("%dMi", preset.MemoryMiB), ClusterId: c.ClusterID, Namespace: ns, ServiceAccount: c.ServiceAccount, StorageClass: c.StorageClass, WorkspaceSize: c.WorkspaceSize, Shell: c.Shell, Uid: c.UID, Gid: c.GID, Scheduling: scheduling, PresetName: preset.Name, PresetCode: preset.Code, GpuSummary: gpuSummary(preset.GPU), GpuSpecDigest: gpuSpecDigest}}
	if gpu != nil && c.GPURuntimeClassName != "" {
		value := c.GPURuntimeClassName
		out.Spec.GpuRuntimeClassName = &value
	}
	if e = pb.ValidateCreateBusiness(out); e != nil {
		return nil, rpcError(e)
	}
	return out, nil
}
func ack(a biz.ACK) *pb.OperationResponse {
	return &pb.OperationResponse{DurableOwnerAck: &attachment.DurableOwnerAck{OperationId: a.OperationID, ResourceId: a.ResourceID, Accepted: a.Accepted}}
}
func (s *Service) AcceptCreate(ctx context.Context, r *pb.AcceptCreateRequest) (*pb.OperationResponse, error) {
	if r == nil || r.Business == nil || r.Business.Spec == nil || r.Business.Data == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	if e := pb.ValidateCreateBusiness(r.Business); e != nil {
		return nil, rpcError(e)
	}
	if r.Business.Spec.ClusterId != s.config.ClusterID {
		return nil, rpcError(biz.ErrInvalid)
	}
	if r.CpuOwnerAttachment != nil {
		return s.acceptCPUCreate(ctx, r)
	}
	if r.GpuOwnerAttachment == nil || r.Business.Spec.ComputeType != biz.TypeGPU {
		return nil, rpcError(biz.ErrInvalid)
	}
	a := r.GpuOwnerAttachment
	ref := a.Ref
	p := a.GpuPlan
	b := r.Business
	f := b.Spec
	d := b.Data
	caller, e := biz.CallerFromContext(ctx)
	if e != nil || a.Actor == nil || caller.Actor != "governance:"+a.Actor.Type+":"+a.Actor.Id {
		return nil, rpcError(biz.ErrDenied)
	}
	if ref == nil || ref.TenantId != r.TenantId || ref.OwnerService != biz.Owner || ref.CreateOperationId != r.OperationId || p == nil || p.Runtime == nil || p.Request == nil || a.MeteringVersion != "gpu-metering-v1" || !proto.Equal(p.Request, b.Gpu) || p.Request.Replicas != 1 || p.Request.DevicesPerReplica < 1 || p.Request.ContainerName != biz.Container || p.Request.ClusterId != f.ClusterId {
		return nil, rpcError(biz.ErrInvalid)
	}
	digest, e := pb.BusinessPayloadDigest(b)
	if e != nil || digest != a.BusinessPayloadDigest {
		return nil, rpcError(biz.ErrInvalid)
	}
	charges, e := validateCharges(p, a.GpuCharges, r.OriginalCharges)
	if e != nil {
		return nil, rpcError(e)
	}
	if e := pb.ValidateGpuBusinessPlan(b, p); e != nil {
		return nil, rpcError(e)
	}
	raw, e := protojson.Marshal(p)
	if e != nil {
		return nil, rpcError(e)
	}
	mapValues := func(values []*acc.KeyValue) (map[string]string, error) {
		out := map[string]string{}
		for _, v := range values {
			if v == nil || v.Key == "" || v.Value == "" || out[v.Key] != "" {
				return nil, biz.ErrInvalid
			}
			out[v.Key] = v.Value
		}
		return out, nil
	}
	nodes, e := mapValues(p.Runtime.NodeLabels)
	if e != nil {
		return nil, rpcError(e)
	}
	annotations, e := mapValues(p.Runtime.PodAnnotations)
	if e != nil {
		return nil, rpcError(e)
	}
	limits, e := mapValues(p.Runtime.LimitsPerContainer)
	if e != nil {
		return nil, rpcError(e)
	}
	runtimeClass := p.Runtime.RuntimeClassName
	if runtimeClass == "" {
		// This value was frozen before Governance signed the business payload.
		// Never consult current owner settings during intake or replay.
		runtimeClass = f.GetGpuRuntimeClassName()
	}
	spec := biz.Spec{InstanceKind: biz.KindContainer, ComputeType: biz.TypeGPU, PresetName: f.PresetName, PresetCode: f.PresetCode, Scheduling: domainScheduling(f.Scheduling), GPUSummary: domainSummary(f.GpuSummary), GPUSpecDigest: f.GpuSpecDigest, Name: d.Name, ImageID: d.ImageId, ImageScope: d.ImageScope, ImageReference: f.ImageReference, ImageDigest: f.ImageDigest, PresetID: d.PresetId, CPU: f.Cpu, Memory: f.Memory, UID: f.Uid, GID: f.Gid, Shell: f.Shell, ServiceAccount: f.ServiceAccount, StorageClass: f.StorageClass, WorkspaceSize: f.WorkspaceSize, ClusterID: f.ClusterId, Namespace: f.Namespace, NotebookName: "instance-" + ref.ResourceId, PVCName: "workspace-" + ref.ResourceId, ImagePullSecret: "ani-instance-image-pull", GPU: biz.GPU{Scheduler: p.Runtime.SchedulerName, Queue: p.Runtime.QueueName, RuntimeClass: runtimeClass, Recipe: p.Runtime.RecipeVersion, Digest: p.ResolutionDigest, NodeLabels: nodes, Annotations: annotations, Limits: limits, Plan: raw}}
	accepted, e := s.repo.AcceptCreate(ctx, biz.Create{TenantID: r.TenantId, ID: ref.ResourceId, OperationID: r.OperationId, RequestHash: a.RequestHash, BusinessDigest: digest, Spec: spec, Charges: charges})
	return ack(accepted), rpcError(e)
}
func validateCharges(p *acc.ResolvedGpuPlan, gpu []*attachment.GpuChargeRef, full []*pb.OriginalQuotaCharge) ([]biz.Charge, error) {
	if _, e := validateGpuPlan(p); e != nil {
		return nil, e
	}
	if p == nil || p.Totals == nil || p.Encoding == nil || len(gpu) != 1 || len(full) == 0 {
		return nil, biz.ErrInvalid
	}
	code := "gpu.shared_memory_mib"
	units := p.Totals.SharedMemoryMib
	if p.Totals.ExclusiveDeviceCount > 0 {
		code = "gpu.physical.count"
		units = p.Totals.ExclusiveDeviceCount
	}
	g := gpu[0]
	if g == nil || g.QuotaCode != code || g.OriginalUnits != units || units <= 0 {
		return nil, biz.ErrInvalid
	}
	seen := map[string]bool{}
	seenCodes := map[string]bool{}
	out := []biz.Charge{}
	found := false
	for _, c := range full {
		if c == nil || !biz.UUID(c.ChargeId) || c.OriginalUnits <= 0 || seen[c.ChargeId] || seenCodes[c.QuotaCode] {
			return nil, biz.ErrInvalid
		}
		seen[c.ChargeId] = true
		seenCodes[c.QuotaCode] = true
		if strings.HasPrefix(c.QuotaCode, "gpu.") {
			if c.ChargeId != g.ChargeId || c.QuotaCode != g.QuotaCode || c.OriginalUnits != g.OriginalUnits {
				return nil, biz.ErrInvalid
			}
			found = true
		}
		out = append(out, biz.Charge{ID: c.ChargeId, Code: c.QuotaCode, Units: c.OriginalUnits})
	}
	if !found {
		return nil, biz.ErrInvalid
	}
	return out, nil
}
func (s *Service) AcceptDelete(ctx context.Context, r *pb.AcceptDeleteRequest) (*pb.OperationResponse, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	if r.CpuOwnerAttachment != nil {
		return s.acceptCPUDelete(ctx, r)
	}
	if r.GpuOwnerAttachment == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	a := r.GpuOwnerAttachment
	ref := a.Ref
	p := a.OriginalGpuPlan
	caller, e := biz.CallerFromContext(ctx)
	if e != nil || a.Actor == nil || caller.Actor != "governance:"+a.Actor.Type+":"+a.Actor.Id {
		return nil, rpcError(biz.ErrDenied)
	}
	if ref == nil || ref.TenantId != r.TenantId || ref.OwnerService != biz.Owner || a.DeleteOperationId != r.OperationId || a.MeteringVersion != "gpu-metering-v1" || r.ExpectedVersion < 1 || p == nil || p.Request == nil || p.Request.Replicas != 1 || p.Request.DevicesPerReplica < 1 || p.Request.ContainerName != biz.Container {
		return nil, rpcError(biz.ErrInvalid)
	}
	charges, e := validateCharges(p, a.OriginalGpuCharges, r.OriginalCharges)
	if e != nil {
		return nil, rpcError(e)
	}
	raw, e := protojson.Marshal(p)
	if e != nil {
		return nil, rpcError(e)
	}
	accepted, e := s.repo.AcceptDelete(ctx, biz.Delete{InstanceKind: biz.KindContainer, ComputeType: biz.TypeGPU, TenantID: r.TenantId, ID: ref.ResourceId, OperationID: r.OperationId, CreateOperationID: ref.CreateOperationId, RequestHash: a.RequestHash, ExpectedVersion: r.ExpectedVersion, GPU: biz.GPU{Digest: p.ResolutionDigest, Plan: raw}, Charges: charges})
	return ack(accepted), rpcError(e)
}
func (s *Service) change(ctx context.Context, r *pb.ChangeInstanceRequest, action string) (*pb.OperationResponse, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	raw, _ := json.Marshal([]any{r.InstanceId, r.ExpectedVersion, action})
	sum := sha256.Sum256(raw)
	accepted, e := s.repo.AcceptCommand(ctx, biz.Command{TenantID: r.TenantId, ID: r.InstanceId, OperationID: uuid.NewString(), Key: r.IdempotencyKey, RequestHash: hex.EncodeToString(sum[:]), Action: action, ExpectedVersion: r.ExpectedVersion})
	return ack(accepted), rpcError(e)
}
func (s *Service) Start(ctx context.Context, r *pb.ChangeInstanceRequest) (*pb.OperationResponse, error) {
	return s.change(ctx, r, "start")
}
func (s *Service) Stop(ctx context.Context, r *pb.ChangeInstanceRequest) (*pb.OperationResponse, error) {
	return s.change(ctx, r, "stop")
}
func wire(i biz.Instance) *pb.Instance {
	return &pb.Instance{InstanceKind: i.Spec.InstanceKind, ComputeType: i.Spec.ComputeType, Id: i.ID, Name: i.Spec.Name, Version: i.Version, DesiredState: i.Desired, ObservedState: i.Observed, Reason: i.Reason, ImageId: i.Spec.ImageID, ImageDigest: i.Spec.ImageDigest, PresetId: i.Spec.PresetID, CreateOperationId: i.CreateOperationID, LastOperationId: i.LastOperationID, ComputeCleaned: i.ComputeCleaned, WorkspaceCleaned: i.WorkspaceCleaned, QuotaReturned: i.QuotaReturned, Cpu: i.Spec.CPU, Memory: i.Spec.Memory, CreatedAt: timestamppb.New(i.CreatedAt), WorkspaceSize: i.Spec.WorkspaceSize, PresetName: i.Spec.PresetName, PresetCode: i.Spec.PresetCode, GpuSummary: wireSummary(i.Spec.GPUSummary), Scheduling: wireScheduling(i.Spec.Scheduling)}
}
func (s *Service) GetInstance(ctx context.Context, r *pb.GetInstanceRequest) (*pb.Instance, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	i, e := s.repo.Get(ctx, r.TenantId, r.InstanceId)
	return wire(i), rpcError(e)
}
func (s *Service) ListInstances(ctx context.Context, r *pb.ListInstancesRequest) (*pb.ListInstancesResponse, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	items, total, e := s.repo.List(ctx, r.TenantId, biz.ListFilter{Page: r.Page, PageSize: r.PageSize, InstanceKind: r.InstanceKind, ComputeType: r.ComputeType, ObservedState: r.ObservedState, Name: r.Name})
	if e != nil {
		return nil, rpcError(e)
	}
	out := &pb.ListInstancesResponse{Items: []*pb.Instance{}, Total: total}
	for _, i := range items {
		out.Items = append(out.Items, wire(i))
	}
	return out, nil
}

func (s *Service) GetOperation(ctx context.Context, r *pb.GetOperationRequest) (*pb.Operation, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	o, e := s.repo.GetOperation(ctx, r.TenantId, r.OperationId)
	return &pb.Operation{Id: o.ID, InstanceId: o.InstanceID, Action: o.Action, IntentVersion: o.Version, State: o.State, Reason: o.Reason}, rpcError(e)
}
func (s *Service) ResolveTerminalTarget(ctx context.Context, r *pb.ResolveTerminalTargetRequest) (*terminal.TerminalTarget, error) {
	if r == nil {
		return nil, rpcError(biz.ErrInvalid)
	}
	if e := biz.RequireTenant(ctx, r.TenantId); e != nil {
		return nil, rpcError(e)
	}
	i, e := s.repo.Get(ctx, r.TenantId, r.InstanceId)
	if e != nil {
		return nil, rpcError(e)
	}
	if i.Desired != biz.Running || i.Observed != biz.Running || i.Version != r.ExpectedVersion || i.NotebookUID == "" || i.PodUID == "" || i.Pending != nil {
		return nil, status.Error(codes.FailedPrecondition, "INSTANCE_TERMINAL_NOT_READY")
	}
	return &terminal.TerminalTarget{TenantId: i.TenantID, InstanceId: i.ID, Version: i.Version, ClusterId: i.Spec.ClusterID, Namespace: i.Spec.Namespace, NotebookName: i.Spec.NotebookName, NotebookUid: i.NotebookUID, PodName: i.PodName, PodUid: i.PodUID, ContainerName: biz.Container, Shell: i.Spec.Shell}, nil
}
