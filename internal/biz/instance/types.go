// Package instance owns the lifecycle of GPU container instances.
package instance

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
)

var (
	ErrInvalid   = errors.New("INVALID_INSTANCE_REQUEST")
	ErrDenied    = errors.New("INSTANCE_SCOPE_DENIED")
	ErrNotFound  = errors.New("INSTANCE_NOT_FOUND")
	ErrConflict  = errors.New("INSTANCE_IDEMPOTENCY_CONFLICT")
	ErrVersion   = errors.New("INSTANCE_VERSION_CONFLICT")
	ErrLease     = errors.New("INSTANCE_LEASE_LOST")
	ErrUnknown   = errors.New("INSTANCE_EXTERNAL_RESULT_UNKNOWN")
	ErrOwnership = errors.New("INSTANCE_OBJECT_OWNERSHIP_CONFLICT")
)

const Owner = "ani-resource-service"
const Container = "workspace"
const (
	Running = "running"
	Stopped = "stopped"
	Deleted = "deleted"
)

type Caller struct{ TenantID, Actor string }
type callerKey struct{}

func WithCaller(ctx context.Context, c Caller) context.Context {
	return context.WithValue(ctx, callerKey{}, c)
}
func CallerFromContext(ctx context.Context) (Caller, error) {
	c, ok := ctx.Value(callerKey{}).(Caller)
	if !ok || c.Actor == "" || !UUID(c.TenantID) {
		return Caller{}, ErrDenied
	}
	return c, nil
}
func RequireTenant(ctx context.Context, tenant string) error {
	c, ok := ctx.Value(callerKey{}).(Caller)
	if !ok || !UUID(tenant) || c.TenantID != tenant || c.Actor == "" {
		return ErrDenied
	}
	return nil
}
func UUID(v string) bool { u, e := uuid.Parse(v); return e == nil && u != uuid.Nil && u.String() == v }

var dnsName = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)

func ResolveNamespace(tenant, trusted, prefix string) (string, error) {
	if !UUID(tenant) {
		return "", ErrInvalid
	}
	ns := trusted
	if ns == "" {
		ns = prefix + tenant
	}
	if len(ns) > 63 || !dnsName.MatchString(ns) {
		return "", fmt.Errorf("%w: invalid namespace", ErrInvalid)
	}
	return ns, nil
}

type Charge struct {
	ID, Code string
	Units    int64
}

// GPU contains the frozen execution values; no memory-factor recomputation occurs.
type GPU struct {
	Scheduler, Queue, RuntimeClass, Recipe, Digest string
	NodeLabels, Annotations, Limits                map[string]string
	Plan                                           json.RawMessage
}
type Spec struct {
	Name, ImageID, ImageScope, ImageReference, ImageDigest string
	PresetID                                               string
	PresetVersion                                          int64
	CPU, Memory                                            string
	UID, GID                                               int64
	Shell, ServiceAccount, StorageClass, WorkspaceSize     string
	ImagePullSecret                                        string
	ClusterID, Namespace, NotebookName, PVCName            string
	GPU                                                    GPU
}
type Create struct {
	TenantID, ID, OperationID, RequestHash, BusinessDigest string
	Spec                                                   Spec
	Charges                                                []Charge
}
type Delete struct {
	TenantID, ID, OperationID, CreateOperationID, RequestHash string
	ExpectedVersion                                           int64
	GPU                                                       GPU
	Charges                                                   []Charge
}
type Command struct {
	TenantID, ID, OperationID, Key, RequestHash, Action string
	ExpectedVersion                                     int64
}
type ACK struct {
	OperationID, ResourceID string
	Accepted                bool
}
type Operation struct {
	TenantID, ID, InstanceID, Action, RequestHash, Key, State, Reason string
	Version                                                           int64
	ACK                                                               ACK
	CreatedAt                                                         time.Time
}
type Instance struct {
	TenantID, ID, CreateOperationID, CreateRequestHash, BusinessDigest string
	Spec                                                               Spec
	Charges                                                            []Charge
	Version                                                            int64
	Desired, Observed, Reason, LastOperationID                         string
	NotebookUID, NotebookRV, PVCUID                                    string
	StatefulSetUID                                                     string
	VolumeName, VolumeUID                                              string
	PodUID, PodName                                                    string
	ComputeCleaned, WorkspaceCleaned, QuotaReturned                    bool
	// Pending is written before sending. Absence of a target never clears an unknown CREATE.
	Pending              *Mutation
	CreatedAt, UpdatedAt time.Time
}
type Mutation struct {
	Kind, OperationID, TargetUID, ResourceVersion string
	Version                                       int64
}
type Work struct {
	Instance   Instance
	Owner      string
	Epoch      int64
	LeaseUntil time.Time
}
type Observation struct {
	NotebookExists, PVCExists, PodExists, PodReady, Stopped bool
	NotebookUID, NotebookRV, PVCUID, PodUID, PodName        string
	StatefulSetUID                                          string
	IntentVersion                                           int64
	VolumeExists                                            bool
	VolumeName, VolumeUID, WorkspaceReason                  string
}
type MutationReceipt struct {
	Mutation Mutation
	Outcome  string
}
type Progress struct {
	Instance Instance
	Next     time.Duration
	Receipt  *MutationReceipt
}
type Repository interface {
	AcceptCreate(context.Context, Create) (ACK, error)
	AcceptDelete(context.Context, Delete) (ACK, error)
	AcceptCommand(context.Context, Command) (ACK, error)
	Get(context.Context, string, string) (Instance, error)
	List(context.Context, string, int, string) ([]Instance, error)
	GetOperation(context.Context, string, string) (Operation, error)
	Claim(context.Context, string, time.Duration) (*Work, error)
	BeginMutation(context.Context, Work, Mutation) error
	Finish(context.Context, Work, Progress) error
}
type Provider interface {
	Observe(context.Context, Instance) (Observation, error)
	EnsureWorkspace(context.Context, Instance) (string, error)
	CreateNotebook(context.Context, Instance) (string, error)
	SetStopped(context.Context, Instance, Observation, bool) error
	DeleteNotebook(context.Context, Instance, Observation) error
	DeleteWorkspace(context.Context, Instance, Observation) error
}
type QuotaReporter interface {
	Report(context.Context, Instance) error
}

type ResolvedImage struct{ Reference, Digest string }
type ImageResolver interface {
	Resolve(context.Context, string, string, string) (ResolvedImage, error)
}
type EnvironmentValidator interface {
	ValidateEnvironment(context.Context, string, string, string, string) error
}

func ValidateCreate(c Create) error {
	if !UUID(c.TenantID) || !UUID(c.ID) || !UUID(c.OperationID) || len(c.RequestHash) != 64 || len(c.BusinessDigest) != 64 || len(c.Charges) == 0 {
		return ErrInvalid
	}
	s := c.Spec
	if len(s.ImageDigest) != 71 || !strings.HasPrefix(s.ImageDigest, "sha256:") || !strings.HasSuffix(s.ImageReference, "@"+s.ImageDigest) {
		return ErrInvalid
	}
	if !UUID(s.ClusterID) || len(s.Namespace) > 63 || !dnsName.MatchString(s.Namespace) || s.NotebookName == "" || s.PVCName == "" || s.ImageReference == "" || s.ImageDigest == "" || s.CPU == "" || s.Memory == "" || s.UID < 0 || s.GID < 0 || s.ServiceAccount == "" || s.StorageClass == "" || s.WorkspaceSize == "" || s.Shell == "" {
		return ErrInvalid
	}
	if s.GPU.Scheduler != "volcano" || s.GPU.Queue == "" || s.GPU.Recipe != "volcano-hami-v1" || len(s.GPU.Digest) != 64 || len(s.GPU.Plan) == 0 || len(s.GPU.Limits) == 0 || len(s.GPU.NodeLabels) == 0 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, q := range c.Charges {
		if !UUID(q.ID) || q.Code == "" || q.Units <= 0 || seen[q.ID] {
			return ErrInvalid
		}
		seen[q.ID] = true
	}
	return nil
}
