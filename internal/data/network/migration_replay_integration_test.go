package data_test

import (
	"context"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
	"github.com/zhangzhe-ctrl/ani-resource-service/tests/testenv"
)

// Replay is checked once against PostgreSQL, rather than repeating migration
// and every table grant in each unrelated repository or process fixture.
func TestNetworkMigrationReplayPreservesLedgerAndRestoresRuntimeGrants(t *testing.T) {
	f := testenv.NewDatabase(t)
	ctx := context.Background()
	ledger := func() string {
		t.Helper()
		var count int
		var snapshot string
		if err := f.Owner.QueryRow(ctx, `SELECT count(*),COALESCE(jsonb_agg(to_jsonb(v) ORDER BY version),'[]')::text FROM network_schema_version v`).Scan(&count, &snapshot); err != nil {
			t.Fatal(err)
		}
		if count == 0 {
			t.Fatal("initial migration did not record any versions")
		}
		return snapshot
	}
	grants := func() string {
		t.Helper()
		var snapshot string
		if err := f.Owner.QueryRow(ctx, `SELECT jsonb_agg(jsonb_build_object(
			'table',c.relname,
			'select',has_table_privilege($1::text,c.oid,'SELECT'),
			'insert',has_table_privilege($1::text,c.oid,'INSERT'),
			'update',has_table_privilege($1::text,c.oid,'UPDATE'),
			'delete',has_table_privilege($1::text,c.oid,'DELETE'),
			'truncate',has_table_privilege($1::text,c.oid,'TRUNCATE'),
			'references',has_table_privilege($1::text,c.oid,'REFERENCES'),
			'trigger',has_table_privilege($1::text,c.oid,'TRIGGER')) ORDER BY c.relname)::text
			FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
			WHERE n.nspname='public' AND c.relkind IN ('r','p')`, f.RuntimeRole).Scan(&snapshot); err != nil {
			t.Fatal(err)
		}
		return snapshot
	}
	checkRole := func(runtime *pgxpool.Pool) {
		t.Helper()
		var role string
		var unsafe, canAssumeOwner bool
		if err := runtime.QueryRow(ctx, `SELECT current_user,
			rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR
			has_schema_privilege(current_user,'public','CREATE') OR
			has_database_privilege(current_user,current_database(),'CREATE') OR
			has_database_privilege(current_user,current_database(),'TEMP'),
			pg_has_role(current_user,$1::text,'SET')
			FROM pg_roles WHERE rolname=current_user`, f.OwnerRole).Scan(&role, &unsafe, &canAssumeOwner); err != nil {
			t.Fatal(err)
		}
		if role != f.RuntimeRole || role == f.OwnerRole || unsafe || canAssumeOwner {
			t.Fatalf("runtime role boundary changed: role=%s unsafe=%t can_assume_owner=%t", role, unsafe, canAssumeOwner)
		}
		if err := f.Repository.CheckReady(ctx); err != nil {
			t.Fatal("runtime readiness after migration:", err)
		}
	}
	runtime, err := pgxpool.New(ctx, f.RuntimeDSN)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(runtime.Close)
	checkRole(runtime)
	beforeLedger, beforeGrants := ledger(), grants()
	role := pgx.Identifier{f.RuntimeRole}.Sanitize()
	// The owner removes only task-owned runtime grants. The production runner
	// must repair them without changing an already applied migration record.
	if _, err := f.Owner.Exec(ctx, "REVOKE SELECT ON network_schema_version FROM "+role+
		"; REVOKE UPDATE ON network_vpcs FROM "+role+
		"; REVOKE INSERT ON network_idempotency FROM "+role); err != nil {
		t.Fatal(err)
	}
	var missingGrants bool
	if err := runtime.QueryRow(ctx, `SELECT
		NOT has_table_privilege(current_user,'network_schema_version','SELECT') AND
		NOT has_table_privilege(current_user,'network_vpcs','UPDATE') AND
		NOT has_table_privilege(current_user,'network_idempotency','INSERT')`).Scan(&missingGrants); err != nil || !missingGrants {
		t.Fatal("fixture did not revoke the selected runtime grants", missingGrants, err)
	}
	if err := data.Migrate(ctx, f.Owner, f.RuntimeRole); err != nil {
		t.Fatal("migration replay:", err)
	}
	if after := ledger(); after != beforeLedger {
		t.Fatalf("migration replay changed versions, checksums or applied times:\nbefore=%s\nafter=%s", beforeLedger, after)
	}
	if after := grants(); after != beforeGrants {
		t.Fatalf("migration replay did not restore the exact runtime grants:\nbefore=%s\nafter=%s", beforeGrants, after)
	}
	checkRole(runtime)
}
