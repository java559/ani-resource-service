package instance

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	quota "github.com/zhangzhe-ctrl/ani-governance/api/quota/gen/go/quota/service/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"google.golang.org/grpc"
)

type QuotaClient interface {
	ReportQuotaRelease(context.Context, *quota.ReportQuotaReleaseRequest, ...grpc.CallOption) (*quota.ReportQuotaReleaseResponse, error)
}
type Quota struct {
	repo   *Postgres
	client QuotaClient
}

func NewQuota(repo *Postgres, client QuotaClient) (*Quota, error) {
	if repo == nil || client == nil {
		return nil, biz.ErrInvalid
	}
	return &Quota{repo, client}, nil
}
func (q *Quota) Report(ctx context.Context, i biz.Instance) error {
	if i.Desired != biz.Deleted || !i.ComputeCleaned || i.Pending != nil {
		return biz.ErrInvalid
	}
	// CPU instances never own a GPU charge or a GPU release outbox record.
	if i.Spec.ComputeType == biz.TypeCPU {
		if !i.Spec.GPU.Empty() || len(i.Charges) != 0 {
			return biz.ErrInvalid
		}
		return nil
	}
	charges := []biz.Charge{}
	for _, c := range i.Charges {
		if strings.HasPrefix(c.Code, "gpu.") {
			charges = append(charges, c)
		}
	}
	if len(charges) == 0 {
		return biz.ErrInvalid
	}
	sort.Slice(charges, func(a, b int) bool { return charges[a].ID < charges[b].ID })
	event := uuid.NewSHA1(uuid.NameSpaceOID, []byte("instance-gpu-release:"+i.TenantID+":"+i.ID+":"+i.CreateOperationID)).String()
	raw, e := json.Marshal(charges)
	if e != nil {
		return e
	}
	// The original immutable payload is persisted before the network call.
	if _, e = q.repo.pool.Exec(ctx, "INSERT INTO instance.instance_quota_release_outbox(tenant_id,instance_id,event_id,original_create_operation_id,charges) VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,instance_id) DO NOTHING", i.TenantID, i.ID, event, i.CreateOperationID, raw); e != nil {
		return e
	}
	var stored []byte
	var storedEvent, create string
	var acknowledged bool
	if e = q.repo.pool.QueryRow(ctx, "SELECT event_id::text,original_create_operation_id::text,charges,acknowledged FROM instance.instance_quota_release_outbox WHERE tenant_id=$1 AND instance_id=$2", i.TenantID, i.ID).Scan(&storedEvent, &create, &stored, &acknowledged); e != nil {
		return e
	}
	var original []biz.Charge
	if e = json.Unmarshal(stored, &original); e != nil {
		return e
	}
	if storedEvent != event || create != i.CreateOperationID || !equalCharges(original, charges) {
		return biz.ErrConflict
	}
	if acknowledged {
		return nil
	}
	request := &quota.ReportQuotaReleaseRequest{ReleaseEventId: event, OperationId: create, Reason: quota.ReleaseReason_RESOURCE_RELEASED, ResourceRefs: []*quota.QuotaResourceRef{{ResourceId: i.ID}}}
	for _, c := range original {
		request.Items = append(request.Items, &quota.QuotaReleaseItem{ChargeId: c.ID, QuotaCode: c.Code, ReleasedTotal: c.Units})
	}
	call, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	reply, e := q.client.ReportQuotaRelease(call, request)
	if e != nil {
		return e
	}
	if reply == nil || len(reply.Items) != len(original) {
		return fmt.Errorf("invalid quota release receipt")
	}
	seen := map[string]bool{}
	for _, r := range reply.Items {
		if r == nil || seen[r.ChargeId] {
			return fmt.Errorf("invalid quota release receipt")
		}
		seen[r.ChargeId] = true
		found := false
		for _, c := range original {
			if c.ID == r.ChargeId && r.ReleasedTotal == c.Units && r.AppliedDelta >= 0 && r.AppliedDelta <= c.Units {
				found = true
			}
		}
		if !found {
			return fmt.Errorf("invalid quota release receipt")
		}
	}
	_, e = q.repo.pool.Exec(ctx, "UPDATE instance.instance_quota_release_outbox SET acknowledged=true WHERE tenant_id=$1 AND instance_id=$2 AND event_id=$3", i.TenantID, i.ID, event)
	return e
}
