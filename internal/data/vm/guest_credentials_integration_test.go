//go:build instanceintegration

package vm

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/GehirnInc/crypt/sha512_crypt"
	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
	"github.com/zhangzhe-ctrl/ani-resource-service/tests/testenv"
)

func TestVMGuestCredentialsDurablePrivateAcceptance(t *testing.T) {
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
	key := bytes.Repeat([]byte{0x37}, 32)
	if e = p.ConfigureGuestCredentialKey(key); e != nil {
		t.Fatal(e)
	}
	a := biz.Acceptance{TenantID: uuid.NewString(), VMID: uuid.NewString(), OperationID: uuid.NewString(), RequestDigest: strings.Repeat("a", 64), Password: "test-only-console-credential", Spec: biz.Spec{Canonical: []byte(`{"fixture":"private-credential-repository"}`), Digest: strings.Repeat("b", 64), GuestInitialization: &biz.GuestInitialization{Method: "CLOUD_INIT_NOCLOUD", Username: "vmuser", PasswordSet: true, CredentialIntentDigest: strings.Repeat("c", 64)}}}
	op, e := p.AcceptCreate(ctx, a)
	if e != nil {
		t.Fatal(e)
	}
	var private []byte
	var ordinary string
	if e = p.pool.QueryRow(ctx, "SELECT credential_ciphertext FROM vm.guest_initializations WHERE tenant_id=$1 AND vm_id=$2", a.TenantID, a.VMID).Scan(&private); e != nil {
		t.Fatal(e)
	}
	if bytes.Contains(private, []byte(a.Password)) || bytes.Contains(private, []byte("$6$")) {
		t.Fatal("credential stored without encryption")
	}
	if e = p.pool.QueryRow(ctx, `SELECT initial_spec::text||current_configuration::text||(SELECT record::text FROM vm.operations WHERE tenant_id=$1 AND id=$3) FROM vm.virtual_machines WHERE tenant_id=$1 AND id=$2`, a.TenantID, a.VMID, op.ID).Scan(&ordinary); e != nil {
		t.Fatal(e)
	}
	if strings.Contains(ordinary, a.Password) || strings.Contains(ordinary, "$6$") || strings.Contains(ordinary, "credential_ciphertext") {
		t.Fatal("credential leaked into ordinary audit records")
	}
	restarted, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer restarted.Close()
	if e = restarted.ConfigureGuestCredentialKey(key); e != nil {
		t.Fatal(e)
	}
	v, e := restarted.Get(ctx, a.TenantID, a.VMID)
	if e != nil {
		t.Fatal(e)
	}
	hash, e := restarted.GuestPasswordHash(ctx, v)
	if e != nil {
		t.Fatal(e)
	}
	if e = sha512_crypt.New().Verify(hash, []byte(a.Password)); e != nil {
		t.Fatal("durable protected hash does not match original password")
	}
	if replay, e := restarted.AcceptCreate(ctx, a); e != nil || replay.ID != op.ID {
		t.Fatal("process restart broke acceptance replay", e)
	}
	changed := a
	changed.Password = "different-console-credential"
	if _, e = restarted.AcceptCreate(ctx, changed); !errors.Is(e, biz.ErrIdempotency) {
		t.Fatalf("same intent metadata changed private password: %v", e)
	}
	foreign := v
	foreign.TenantID = uuid.NewString()
	if _, e = restarted.GuestPasswordHash(ctx, foreign); !errors.Is(e, biz.ErrNotFound) {
		t.Fatal("cross-tenant credential access", e)
	}
	wrong, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer wrong.Close()
	if e = wrong.ConfigureGuestCredentialKey(bytes.Repeat([]byte{0x49}, 32)); e != nil {
		t.Fatal(e)
	}
	if _, e = wrong.GuestPasswordHash(ctx, v); !errors.Is(e, biz.ErrChanged) {
		t.Fatal("wrong key did not fail closed", e)
	}
}
