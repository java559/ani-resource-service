//go:build instanceintegration

package vm

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

// vmImageDatabase provisions an isolated database whose owner applies the VM
// migrations, plus the distinct runtime role Migrate() demands. The shared testenv
// fixture is deliberately not used: it always runs the network schema migrations, and
// the image lane needs only the vm schema, so a failure there would not be evidence
// about this feature.
type vmImageDatabase struct {
	OwnerDSN, RuntimeDSN, RuntimeRole string
}

func newVMImageDatabase(t *testing.T) *vmImageDatabase {
	t.Helper()
	dsn := os.Getenv("VM_IMAGE_TEST_ADMIN_DSN")
	if dsn == "" {
		t.Skip("VM_IMAGE_TEST_ADMIN_DSN is required: an isolated PostgreSQL owned by this task")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(admin.Close)
	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")[:16]
	ownerRole, runtimeRole := "vmimg_owner_"+suffix, "vmimg_runtime_"+suffix
	address, err := url.Parse(dsn)
	if err != nil {
		t.Fatal("invalid admin DSN")
	}
	password, _ := address.User.Password()
	literal := "'" + strings.ReplaceAll(password, "'", "''") + "'"
	for _, role := range []string{ownerRole, runtimeRole} {
		if _, err = admin.Exec(ctx, "CREATE ROLE "+pgx.Identifier{role}.Sanitize()+" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD "+literal); err != nil {
			t.Fatal(err)
		}
		name := pgx.Identifier{role}.Sanitize()
		t.Cleanup(func() {
			_, _ = admin.Exec(context.Background(), "DROP ROLE "+name)
		})
	}
	name := pgx.Identifier{"vmimg_test_" + suffix}.Sanitize()
	if _, err = admin.Exec(ctx, "CREATE DATABASE vmimg_test_"+suffix+" OWNER "+pgx.Identifier{ownerRole}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = admin.Exec(context.Background(), "DROP DATABASE "+name+" WITH (FORCE)")
	})
	address.Path = "/vmimg_test_" + suffix
	address.User = url.UserPassword(ownerRole, password)
	owner := address.String()
	address.User = url.UserPassword(runtimeRole, password)
	return &vmImageDatabase{OwnerDSN: owner, RuntimeDSN: address.String(), RuntimeRole: runtimeRole}
}

func imageFixture(tenant, id string) biz.Image {
	return biz.Image{TenantID: tenant, ID: id, MediaKey: biz.ImageMediaKey(id), Kind: "DISK_IMAGE", Format: "qcow2",
		Name: "integration image", Description: "kept", State: biz.StatePreparing, CleanupState: biz.CleanupNone,
		SourceCapacityGB: 8, Version: 1,
		OS: biz.ImageOS{OSFamily: "linux", OSVersion: "24.04", Architecture: "amd64", Firmware: "bios", RootBus: "virtio", DataBus: "scsi", NICModel: "virtio"}}
}

func imageCommand(t *testing.T, action, tenant, id, key string, version int64, mask []string, fields any) biz.ImageCommand {
	t.Helper()
	digest, e := biz.ImageRequestDigest(action, tenant, id, fields, version, mask)
	if e != nil {
		t.Fatal(e)
	}
	// Intent is the normalized business payload the digest was computed over; the
	// command is invalid without it, so the fixture carries the same rule.
	intent, e := json.Marshal(fields)
	if e != nil {
		t.Fatal(e)
	}
	return biz.ImageCommand{TenantID: tenant, ImageID: id, Action: action, Key: key, RequestDigest: digest, ExpectedVersion: version, Mask: mask, Intent: intent}
}

func TestVMImageMigrateIsRepeatableAndGrantsRuntimeAccess(t *testing.T) {
	ctx := context.Background()
	db := newVMImageDatabase(t)
	for round := 1; round <= 2; round++ {
		if e := Migrate(ctx, db.OwnerDSN, db.RuntimeRole); e != nil {
			t.Fatalf("round %d: %v", round, e)
		}
	}
	p, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close()
	pool, e := pgxpool.New(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	var versions []int64
	rows, e := pool.Query(ctx, "SELECT version FROM vm.schema_version ORDER BY version")
	if e != nil {
		t.Fatal(e)
	}
	for rows.Next() {
		var v int64
		if e = rows.Scan(&v); e != nil {
			t.Fatal(e)
		}
		versions = append(versions, v)
	}
	rows.Close()
	if len(versions) != 4 || versions[3] != 4 {
		t.Fatalf("schema_version = %v, want 1..4", versions)
	}
	for _, table := range []string{"images", "image_requests", "image_source_references"} {
		var granted int
		if e = pool.QueryRow(ctx, `SELECT count(DISTINCT privilege_type) FROM information_schema.role_table_grants
		 WHERE table_schema='vm' AND table_name=$1 AND grantee=current_user`, table).Scan(&granted); e != nil {
			t.Fatal(e)
		}
		if granted != 4 {
			t.Errorf("%s grants for the runtime role = %d, want SELECT/INSERT/UPDATE/DELETE", table, granted)
		}
	}
}

func TestVMImageCreateReplayConflictAndTenantIsolation(t *testing.T) {
	ctx := context.Background()
	db := newVMImageDatabase(t)
	if e := Migrate(ctx, db.OwnerDSN, db.RuntimeRole); e != nil {
		t.Fatal(e)
	}
	p, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close()
	tenant, id := uuid.NewString(), uuid.NewString()
	command := imageCommand(t, biz.ImageCreateAction, tenant, id, uuid.NewString(), 0, nil,
		struct{ Name string }{"integration image"})

	const racers = 6
	var wg sync.WaitGroup
	results := make(chan biz.Image, racers)
	errs := make(chan error, racers)
	for i := 0; i < racers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got, e := p.CreateImage(ctx, imageFixture(tenant, id), command)
			if e == nil {
				results <- got
				return
			}
			errs <- e
		}()
	}
	wg.Wait()
	close(results)
	close(errs)
	for e := range errs {
		t.Fatalf("concurrent create: %v", e)
	}
	count := 0
	for got := range results {
		count++
		if got.ID != id || got.State != biz.StatePreparing || got.MediaKey != biz.ImageMediaKey(id) {
			t.Fatalf("replay produced %s/%s, want the same record", got.ID, got.MediaKey)
		}
		if got.UploadDeadline == nil {
			t.Error("created record has no upload deadline")
		}
	}
	if count != racers {
		t.Fatalf("only %d/%d racers got a result", count, racers)
	}
	var rows int
	pool, e := pgxpool.New(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	if e = pool.QueryRow(ctx, "SELECT count(*) FROM vm.images").Scan(&rows); e != nil {
		t.Fatal(e)
	}
	if rows != 1 {
		t.Fatalf("row count after concurrent create = %d, want 1", rows)
	}

	// Same key, different business fields must conflict rather than overwrite.
	other := imageCommand(t, biz.ImageCreateAction, tenant, id, command.Key, 0, nil, struct{ Name string }{"renamed"})
	if _, e = p.CreateImage(ctx, imageFixture(tenant, id), other); !errors.Is(e, biz.ErrImageIdempotency) {
		t.Errorf("digest change accepted: %v", e)
	}
	// Same key pointed at another image must conflict too.
	foreignID := uuid.NewString()
	targetSwitch := imageCommand(t, biz.ImageCreateAction, tenant, foreignID, command.Key, 0, nil, struct{ Name string }{"integration image"})
	if _, e = p.CreateImage(ctx, imageFixture(tenant, foreignID), targetSwitch); !errors.Is(e, biz.ErrImageIdempotency) {
		t.Errorf("target swap under a used key accepted: %v", e)
	}

	// Another tenant cannot read or resolve the record at all.
	outsider := uuid.NewString()
	if _, e = p.FindImage(ctx, outsider, id); !errors.Is(e, biz.ErrNotFound) {
		t.Errorf("cross-tenant read leaked: %v", e)
	}
	if _, e = p.ResolveImageMedia(ctx, outsider, biz.ImageMediaKey(id)); !errors.Is(e, biz.ErrImageNotFound) {
		t.Errorf("cross-tenant media key resolved: %v", e)
	}
	// A non-READY record is never consumable, even by its own tenant.
	if _, e = p.ResolveImageMedia(ctx, tenant, biz.ImageMediaKey(id)); !errors.Is(e, biz.ErrImageNotReady) {
		t.Errorf("PREPARING record resolved for use: %v", e)
	}
	items, total, e := p.ListImages(ctx, outsider, biz.ImageListFilter{})
	if e != nil || total != 0 || len(items) != 0 {
		t.Errorf("cross-tenant list = %d/%d, want empty (%v)", len(items), total, e)
	}
}

func TestVMImageUpdateVersionCASAndFieldWhitelist(t *testing.T) {
	ctx := context.Background()
	db := newVMImageDatabase(t)
	if e := Migrate(ctx, db.OwnerDSN, db.RuntimeRole); e != nil {
		t.Fatal(e)
	}
	p, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close()
	tenant, id := uuid.NewString(), uuid.NewString()
	create := imageCommand(t, biz.ImageCreateAction, tenant, id, uuid.NewString(), 0, nil, struct{ Name string }{"integration image"})
	if _, e = p.CreateImage(ctx, imageFixture(tenant, id), create); e != nil {
		t.Fatal(e)
	}
	key := uuid.NewString()
	update := imageCommand(t, biz.ImageUpdateAction, tenant, id, key, 1, []string{"name", "description"},
		struct {
			Name, Description string
		}{"renamed", ""})
	got, e := p.UpdateImage(ctx, update, "renamed", "", []string{"name", "description"})
	if e != nil {
		t.Fatalf("update: %v", e)
	}
	if got.Name != "renamed" || got.Description != "" || got.Version != 2 {
		t.Fatalf("update result = %q/%q/v%d", got.Name, got.Description, got.Version)
	}
	// Replay of the accepted write must not be rejected by the advanced version.
	replay, e := p.UpdateImage(ctx, update, "renamed", "", []string{"name", "description"})
	if e != nil || replay.Version != 2 || replay.Name != "renamed" {
		t.Errorf("replay of accepted update rejected or drifted: %v %+v", e, replay)
	}
	stale := imageCommand(t, biz.ImageUpdateAction, tenant, id, uuid.NewString(), 1, []string{"name"}, struct{ Name string }{"again"})
	if _, e = p.UpdateImage(ctx, stale, "again", "", []string{"name"}); !errors.Is(e, biz.ErrImageVersion) {
		t.Errorf("stale version accepted: %v", e)
	}
	// An unknown mask path never reaches storage.
	bad := imageCommand(t, biz.ImageUpdateAction, tenant, id, uuid.NewString(), 2, []string{"state"}, struct{ Name string }{"x"})
	if _, e = p.UpdateImage(ctx, bad, "x", "", []string{"state"}); !errors.Is(e, biz.ErrImageInvalid) {
		t.Errorf("state mask accepted: %v", e)
	}
	// A name-only write must keep the description untouched.
	nameOnly := imageCommand(t, biz.ImageUpdateAction, tenant, id, uuid.NewString(), 2, []string{"name"}, struct{ Name string }{"third"})
	current, e := p.FindImage(ctx, tenant, id)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = p.UpdateImage(ctx, nameOnly, "third", current.Description, []string{"name"}); e != nil {
		t.Fatal(e)
	}
	current, e = p.FindImage(ctx, tenant, id)
	if e != nil {
		t.Fatal(e)
	}
	if current.Name != "third" || current.State != biz.StatePreparing {
		t.Fatalf("name-only write changed the record: %+v", current)
	}
}

func TestVMImageDeleteRefusesWhilePinnedAndRecordsTombstone(t *testing.T) {
	ctx := context.Background()
	db := newVMImageDatabase(t)
	if e := Migrate(ctx, db.OwnerDSN, db.RuntimeRole); e != nil {
		t.Fatal(e)
	}
	p, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close()
	tenant, imageID, vmID := uuid.NewString(), uuid.NewString(), uuid.NewString()
	create := imageCommand(t, biz.ImageCreateAction, tenant, imageID, uuid.NewString(), 0, nil, struct{ Name string }{"integration image"})
	_, e = p.CreateImage(ctx, imageFixture(tenant, imageID), create)
	if e != nil {
		t.Fatal(e)
	}
	pool, e := pgxpool.New(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()

	// Only a READY record can be pinned, so publish the evidence first.
	if _, e = pool.Exec(ctx, `UPDATE vm.images SET state='READY', source_sha256=$3, native='{"Namespace":"ns","PVCName":"pvc","PVCUID":"pvc-uid","SourceVolumeMode":"Block"}'::jsonb WHERE tenant_id=$1 AND id=$2`, tenant, imageID, strings.Repeat("a", 64)); e != nil {
		t.Fatal(e)
	}
	ready, e := p.FindImage(ctx, tenant, imageID)
	if e != nil {
		t.Fatal(e)
	}
	ref, e := biz.NewImageReference(biz.VM{TenantID: tenant, ID: vmID}, uuid.NewString(), "SYSTEM_DISK", ready.Source())
	if e != nil {
		t.Fatal(e)
	}
	// A pin is anchored to a real VM row, so the fixture creates one the same way
	// AcceptCreate would: an id, a tenant and a minimal frozen configuration.
	frozen, e := json.Marshal(biz.VM{TenantID: tenant, ID: vmID, Version: 1, Desired: "Stopped", Observed: "Unknown"})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = pool.Exec(ctx, `INSERT INTO vm.virtual_machines(tenant_id,id,create_operation_id,request_digest,spec_digest,initial_spec,current_configuration,version)
		VALUES($1,$2,$3,$4,$5,$6,$6,1)`, tenant, vmID, uuid.NewString(), strings.Repeat("1", 64), strings.Repeat("2", 64), frozen); e != nil {
		t.Fatalf("fixture VM row: %v", e)
	}
	tx, e := pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	defer tx.Rollback(ctx)
	if e = p.referenceImageRowsLocked(ctx, tx, []biz.ImageReference{ref}); e != nil {
		t.Fatalf("READY pin refused: %v", e)
	}
	if e = insertImageReferences(ctx, tx, []biz.ImageReference{ref}); e != nil {
		t.Fatalf("pin insert: %v", e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	// Re-admitting the same consumer must be a no-op, not a duplicate row: the
	// re-attach path reuses the frozen identity and never re-pins a released copy.
	tx2, e := pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	defer tx2.Rollback(ctx)
	if e = insertImageReferences(ctx, tx2, []biz.ImageReference{ref}); e != nil {
		t.Fatalf("re-pin: %v", e)
	}
	if e = tx2.Commit(ctx); e != nil {
		t.Fatalf("re-pin commit: %v", e)
	}
	if held, e := p.CountHeldReferences(ctx, tenant, imageID); e != nil || held != 1 {
		t.Fatalf("held count after re-pin = %d (%v), want 1", held, e)
	}
	if held, e := p.CountHeldReferences(ctx, tenant, imageID); e != nil || held != 1 {
		t.Fatalf("held count = %d (%v)", held, e)
	}
	// The two reference *listings* are the only image queries the CRUD tests do not
	// share with the workers, and their ORDER BY list is independent of the column
	// projection: a column that the table does not have aborts the whole service at
	// startup, because the reference worker runs whether or not uploads are enabled.
	page, e := p.ListHeldReferences(ctx, 10)
	if e != nil {
		t.Fatalf("held reference page: %v", e)
	}
	found := false
	for _, r := range page {
		if r.ConsumerID == ref.ConsumerID && r.State == "HELD" {
			found = true
		}
	}
	if !found {
		t.Errorf("pin absent from worker page: %+v", page)
	}
	listed, e := p.ListImageReferences(ctx, tenant, imageID)
	if e != nil {
		t.Fatalf("image reference list: %v", e)
	}
	if len(listed) != 1 || listed[0].VMID != vmID {
		t.Errorf("image reference list = %+v, want the single pin", listed)
	}

	delete1 := imageCommand(t, biz.ImageDeleteAction, tenant, imageID, uuid.NewString(), ready.Version, nil, struct{}{})
	if _, e = p.DeleteImage(ctx, delete1); !errors.Is(e, biz.ErrImageInUse) {
		t.Fatalf("pinned delete accepted: %v", e)
	}
	unchanged, e := p.FindImage(ctx, tenant, imageID)
	if e != nil || unchanged.State != biz.StateReady {
		t.Fatalf("refused delete changed the state: %v %+v", e, unchanged)
	}

	// The pin releases only on proven completion, then deletion can proceed.
	if e = p.ReleaseImageReference(ctx, ref, biz.ReferenceReleasedClone); e != nil {
		t.Fatalf("release: %v", e)
	}
	if e = p.ReleaseImageReference(ctx, ref, biz.ReferenceReleasedClone); !errors.Is(e, biz.ErrImageVersion) {
		t.Errorf("double release accepted: %v", e)
	}
	current, e := p.FindImage(ctx, tenant, imageID)
	if e != nil {
		t.Fatal(e)
	}
	delete2 := imageCommand(t, biz.ImageDeleteAction, tenant, imageID, uuid.NewString(), current.Version, nil, struct{}{})
	deleted, e := p.DeleteImage(ctx, delete2)
	if e != nil || deleted.State != biz.StateDeleting || deleted.CleanupState != biz.CleanupPending {
		t.Fatalf("delete after release = %+v (%v)", deleted, e)
	}
	// The refused attempt must not leak a second request row under a new key.
	replay, e := p.DeleteImage(ctx, delete2)
	if e != nil || replay.State != biz.StateDeleting {
		t.Errorf("delete replay: %v %+v", e, replay)
	}
	// A deleted record never resolves for use again.
	if _, e = pool.Exec(ctx, `UPDATE vm.images SET state='DELETED' WHERE tenant_id=$1 AND id=$2`, tenant, imageID); e != nil {
		t.Fatal(e)
	}
	if _, e = p.ResolveImageMedia(ctx, tenant, biz.ImageMediaKey(imageID)); !errors.Is(e, biz.ErrImageNotFound) {
		t.Errorf("deleted record resolved for use: %v", e)
	}
	items, total, e := p.ListImages(ctx, tenant, biz.ImageListFilter{})
	if e != nil || total != 0 || len(items) != 0 {
		t.Errorf("tombstone listed: %d/%d (%v)", len(items), total, e)
	}
	if _, _, e := p.ListImages(ctx, tenant, biz.ImageListFilter{State: "nonsense"}); !errors.Is(e, biz.ErrImageInvalid) {
		t.Errorf("unknown state filter accepted: %v", e)
	}
}

func TestVMImageClaimLeaseAndDeadlineCAS(t *testing.T) {
	ctx := context.Background()
	db := newVMImageDatabase(t)
	if e := Migrate(ctx, db.OwnerDSN, db.RuntimeRole); e != nil {
		t.Fatal(e)
	}
	p, e := OpenPostgres(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close()
	pool, e := pgxpool.New(ctx, db.RuntimeDSN)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	tenant, expiring, idle := uuid.NewString(), uuid.NewString(), uuid.NewString()
	for _, id := range []string{expiring, idle} {
		command := imageCommand(t, biz.ImageCreateAction, tenant, id, uuid.NewString(), 0, nil, struct{ Name string }{"integration image"})
		if _, e = p.CreateImage(ctx, imageFixture(tenant, id), command); e != nil {
			t.Fatal(e)
		}
	}
	if _, e = pool.Exec(ctx, `UPDATE vm.images SET upload_deadline=now()-interval '1 second' WHERE tenant_id=$1 AND id=$2`, tenant, expiring); e != nil {
		t.Fatal(e)
	}
	token := uuid.NewString()
	claimed, hold, e := p.ClaimImage(ctx, token, 30*time.Second)
	if e != nil || !hold {
		t.Fatalf("claim: %v %v", e, hold)
	}
	if claimed.ID != expiring {
		t.Fatalf("claimed %s, want the expired record %s", claimed.ID, expiring)
	}
	// Both records are due, so a second worker legitimately takes the other one;
	// it must never take the record still under the first lease.
	second, hold, e := p.ClaimImage(ctx, uuid.NewString(), 30*time.Second)
	if e != nil || !hold || second.ID == claimed.ID {
		t.Fatalf("second claim = %v %+v (%v)", hold, second, e)
	}
	// With both leases live there is nothing left to hand out: an empty claim is the
	// expected result, and it must not be a lease stolen from another worker.
	if _, hold, e = p.ClaimImage(ctx, uuid.NewString(), 30*time.Second); hold || (e != nil && !errors.Is(e, biz.ErrNotFound)) {
		t.Errorf("a leased record was handed out again: hold=%v err=%v", hold, e)
	}
	// The expired record must land in FAILED with cleanup queued, never UNAVAILABLE.
	if current, e := p.FindImage(ctx, tenant, expiring); e != nil || current.State != biz.StateFailed ||
		current.Reason != "UPLOAD_DEADLINE_EXCEEDED" || current.CleanupState != biz.CleanupPending || current.UploadDeadline != nil {
		t.Errorf("deadline CAS result = %+v (%v)", current, e)
	}
	// A record without a deadline is never collected by the same path.
	if current, e := p.FindImage(ctx, tenant, idle); e != nil || current.State != biz.StatePreparing {
		t.Errorf("unexpired record moved: %+v (%v)", current, e)
	}
	// SaveImage refuses a writer whose lease or state went stale.
	stale := claimed
	stale.Lease = uuid.NewString()
	if e = p.SaveImage(ctx, stale); e == nil {
		t.Error("a lease-less save succeeded")
	}
}
