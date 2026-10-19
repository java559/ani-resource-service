//go:build instanceintegration

package instance_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
	"strings"
	"testing"
	"time"
)

func TestCurrentConfigurationPGRejectsRetiredRowsAndAcceptsCurrent(t *testing.T) {
	for _, version := range []uint32{0, 1, 2} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			f := database(t)
			c := create()
			c.Spec.ContractVersion = 2
			c.Spec.WorkspaceMountPath = "/workspace"
			ctx := context.Background()
			if _, err := f.repo.AcceptCreate(ctx, c); err != nil {
				t.Fatal(err)
			}
			value, err := f.repo.Get(ctx, c.TenantID, c.ID)
			if err != nil || value.Spec.ContractVersion != 2 {
				t.Fatal("current create did not persist", err)
			}
			// Corrupt only this exclusive fixture row: the column must not authorize an
			// old snapshot, nor may a current snapshot hide a retired column value.
			value.Spec.ContractVersion = version
			if version < 2 {
				value.Spec.WorkspaceMountPath = ""
			}
			raw, err := json.Marshal(value)
			if err != nil {
				t.Fatal(err)
			}
			column := 2
			if version == 2 {
				column = 1
			}
			if _, err = f.runtime.Exec(ctx, `UPDATE instance.instances SET snapshot=$3,instance_contract_version=$4 WHERE tenant_id=$1 AND instance_id=$2`, c.TenantID, c.ID, raw, column); err != nil {
				t.Fatal(err)
			}
			if _, err = f.repo.Get(ctx, c.TenantID, c.ID); !errors.Is(err, biz.ErrInvalid) {
				t.Fatal("retired or mismatched persisted contract was read")
			}
			if _, _, err = f.repo.List(biz.WithCaller(ctx, biz.Caller{TenantID: c.TenantID, Actor: "governance:user:42"}), c.TenantID, biz.ListFilter{}); !errors.Is(err, biz.ErrInvalid) {
				t.Fatal("retired persisted contract appeared in list")
			}
			if _, err = f.repo.Claim(ctx, "current-contract", time.Second); !errors.Is(err, biz.ErrInvalid) {
				t.Fatal("retired persisted contract entered worker")
			}
			rejected := c
			rejected.Spec.ContractVersion = version
			if version < 2 {
				rejected.Spec.WorkspaceMountPath = ""
				if _, err = f.repo.AcceptCreate(ctx, rejected); err == nil {
					t.Fatal("retired create was accepted")
				}
			}
		})
	}
}

func TestCurrentConfigurationPGDeleteFirstUsesCurrentContract(t *testing.T) {
	for _, kind := range []string{biz.TypeCPU, biz.TypeGPU} {
		t.Run(kind, func(t *testing.T) {
			f := database(t)
			ctx := context.Background()
			c := create()
			c.Spec.ComputeType = kind
			d := biz.Delete{InstanceKind: biz.KindContainer, ComputeType: kind, TenantID: c.TenantID, ID: c.ID, OperationID: uuid.NewString(), CreateOperationID: c.OperationID, RequestHash: strings.Repeat("e", 64)}
			if _, err := f.repo.AcceptDelete(ctx, d); err != nil {
				t.Fatal(err)
			}
			i, err := f.repo.Get(ctx, c.TenantID, c.ID)
			if err != nil || i.Spec.ContractVersion != 2 || i.Spec.WorkspaceMountPath != "/workspace" || i.CreateRequestHash != "" {
				t.Fatal("delete-first did not persist a current empty-create fence", err)
			}
			if _, err := f.repo.AcceptDelete(ctx, d); err != nil {
				t.Fatal("current delete-first replay failed", err)
			}
		})
	}
}
