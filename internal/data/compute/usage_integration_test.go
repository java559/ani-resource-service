//go:build instanceintegration

package compute_test

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/compute"
	compute "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/compute"
)

func TestVirtualMachineUsageReadsSharedCatalogWithRealPostgres(t *testing.T) {
	f := migrationDatabase(t)
	ctx := context.Background()
	if err := compute.Migrate(ctx, f.ownerDSN, f.role); err != nil {
		t.Fatal(err)
	}
	cluster, tenant, preset, request := uuid.NewString(), uuid.NewString(), uuid.NewString(), uuid.NewString()
	exec(t, f.owner, `INSERT INTO compute.presets(id,code,name,compute_type,cpu_millis,memory_mib,enabled,created_by,updated_by) VALUES($1,'vm-shared','Shared CPU','cpu',1500,1536,true,'governance:user:42','governance:user:42')`, preset)
	repo, err := compute.OpenPostgres(ctx, f.runtimeDSN)
	if err != nil {
		t.Fatal(err)
	}
	defer repo.Close()
	resolver, err := biz.NewResolver(repo, cluster)
	if err != nil {
		t.Fatal(err)
	}
	scope := func(method string, usage biz.Usage) context.Context {
		return biz.WithScope(ctx, biz.Scope{TenantID: tenant, Actor: "governance:user:42", RequestID: request, Workload: "ani-governance", Method: method, Usage: usage})
	}
	var first biz.Selection
	for _, usage := range []biz.Usage{biz.ContainerInstance, biz.Training, biz.Inference, biz.VirtualMachine} {
		got, err := resolver.Resolve(scope(biz.ResolveMethod, usage), tenant, preset, usage)
		if err != nil {
			t.Fatal("catalog resolution", usage, err)
		}
		if first.PresetID != "" && !reflect.DeepEqual(first, got) {
			t.Fatal("usage changed the same persisted preset")
		}
		first = got
	}
	vmContext := scope(biz.VMResolveMethod, biz.VirtualMachine)
	if got, err := resolver.Resolve(vmContext, tenant, preset, biz.VirtualMachine); err != nil || !reflect.DeepEqual(first, got) {
		t.Fatal("VM internal read scope", err)
	}
	if _, err := resolver.Resolve(vmContext, uuid.NewString(), preset, biz.VirtualMachine); !errors.Is(err, biz.ErrDenied) {
		t.Fatal("VM cross-tenant resolution accepted", err)
	}
	listed, total, _, err := repo.ListPresets(scope(biz.ListMethod, biz.VirtualMachine), biz.PresetFilter{Tenant: true})
	if err != nil || total != 1 || len(listed) != 1 || listed[0].ID != preset {
		t.Fatal("VM directory did not read the shared catalog", err)
	}
	exec(t, f.owner, `UPDATE compute.presets SET enabled=false WHERE id=$1`, preset)
	if _, err := resolver.Resolve(vmContext, tenant, preset, biz.VirtualMachine); !errors.Is(err, biz.ErrPresetDisabled) {
		t.Fatal("disabled persisted preset still selectable", err)
	}
}
