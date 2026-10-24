//go:build instanceintegration

package vm

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	"github.com/zhangzhe-ctrl/ani-resource-service/tests/testenv"
)

type powerVMNative struct {
	biz.NativePort
	ref              biz.ObjectRef
	vmi              string
	starts, restarts int
}

func (n *powerVMNative) ValidateSource(context.Context, biz.Source) error        { return nil }
func (n *powerVMNative) EnsureVM(context.Context, biz.VM) (biz.ObjectRef, error) { return n.ref, nil }
func (n *powerVMNative) Observe(context.Context, biz.VM) (biz.Observation, error) {
	x := biz.Observation{VM: n.ref, Stopped: n.vmi == "", Running: n.vmi != "", State: "Stopped"}
	if n.vmi != "" {
		x.State = "Running"
		x.VMI = &biz.ObjectRef{UID: n.vmi}
	}
	return x, nil
}
func (n *powerVMNative) Start(context.Context, biz.VM) error {
	n.starts++
	if n.starts == 1 {
		return biz.ErrUnknown
	}
	n.vmi = uuid.NewString()
	return nil
}
func (n *powerVMNative) Restart(context.Context, biz.VM) error { n.restarts++; return biz.ErrUnknown }

type powerVMNetwork struct{ preparingVMNetwork }

func (n powerVMNetwork) Observe(context.Context, biz.VM, biz.Observation) ([]biz.Address, error) {
	return nil, nil
}

func TestVMPowerUnknownRecoveryDoesNotRepeatRestart(t *testing.T) {
	ctx := context.Background()
	db := testenv.NewDatabase(t)
	if e := Migrate(ctx, db.Owner.Config().ConnString(), db.RuntimeRole); e != nil {
		t.Fatal(e)
	}
	p, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close()
	tenant, id, op := uuid.NewString(), uuid.NewString(), uuid.NewString()
	_, e = p.AcceptCreate(ctx, biz.Acceptance{TenantID: tenant, VMID: id, OperationID: op, RequestDigest: stringDigest('a'), Spec: biz.Spec{Canonical: []byte(`{"power":"native-boundary"}`), Digest: stringDigest('b'), Name: "power", StartAfterCreate: true}})
	if e != nil {
		t.Fatal(e)
	}
	native := &powerVMNative{ref: biz.ObjectRef{Kind: "VirtualMachine", UID: uuid.NewString(), Name: "vm-power"}}
	w, e := biz.NewWorker(p, native, powerVMNetwork{})
	if e != nil {
		t.Fatal(e)
	}
	step := func() {
		t.Helper()
		until := time.Now().Add(3 * time.Second)
		for time.Now().Before(until) {
			worked, err := w.Step(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if worked {
				return
			}
			time.Sleep(30 * time.Millisecond)
		}
		t.Fatal("durable operation never became due")
	}
	step()
	step()
	step() // network, VM, first start loses its result
	if native.starts != 1 {
		t.Fatalf("first start count %d", native.starts)
	}
	// Reopen PostgreSQL as a new worker, retaining the original operation/key.
	reopened, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer reopened.Close()
	w, e = biz.NewWorker(reopened, native, powerVMNetwork{})
	if e != nil {
		t.Fatal(e)
	}
	step()
	step()
	saved, e := reopened.GetOperation(ctx, tenant, id, op)
	if e != nil || saved.State != "succeeded" || native.starts != 2 {
		t.Fatalf("observed-stopped Start did not recover: %+v starts=%d err=%v", saved, native.starts, e)
	}
	restart, e := reopened.AcceptCommand(ctx, biz.Command{TenantID: tenant, VMID: id, Action: "restart", Key: "restart-one", RequestDigest: stringDigest('c'), Intent: []byte(`{"expected_version":1}`), ExpectedVersion: 1})
	if e != nil {
		t.Fatal(e)
	}
	step()
	step()
	pending, e := reopened.GetOperation(ctx, tenant, id, restart.ID)
	if e != nil || pending.State != "unknown" || native.restarts != 1 {
		t.Fatalf("unknown Restart was resent: %+v count=%d err=%v", pending, native.restarts, e)
	}
	native.vmi = uuid.NewString()
	step()
	saved, e = reopened.GetOperation(ctx, tenant, id, restart.ID)
	if e != nil || saved.State != "succeeded" || native.restarts != 1 {
		t.Fatalf("new VMI not observed for original Restart: %+v count=%d err=%v", saved, native.restarts, e)
	}
}
