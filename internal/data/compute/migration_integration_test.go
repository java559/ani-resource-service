//go:build instanceintegration

package compute_test

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	compute "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/compute"
	instance "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/instance"
)

type migrationFixture struct {
	owner                      *pgxpool.Pool
	ownerDSN, runtimeDSN, role string
}

func migrationDatabase(t *testing.T) migrationFixture {
	t.Helper()
	ctx := context.Background()
	raw, err := os.ReadFile(os.Getenv("INSTANCE_TEST_ADMIN_DSN_FILE"))
	if err != nil {
		t.Fatal("exclusive database admin DSN file required")
	}
	u, err := url.Parse(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatal("invalid fixture DSN")
	}
	admin, err := pgxpool.New(ctx, u.String())
	if err != nil {
		t.Fatal("fixture admin unavailable")
	}
	t.Cleanup(admin.Close)
	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")
	db, owner, role := "compute_test_"+suffix, "compute_owner_"+suffix, "compute_runtime_"+suffix
	password, _ := u.User.Password()
	literal := "'" + strings.ReplaceAll(password, "'", "''") + "'"
	for _, r := range []string{owner, role} {
		if _, err = admin.Exec(ctx, "CREATE ROLE "+pgx.Identifier{r}.Sanitize()+" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD "+literal); err != nil {
			t.Fatal("fixture role creation failed")
		}
		r := r
		t.Cleanup(func() {
			if _, err := admin.Exec(ctx, "DROP ROLE "+pgx.Identifier{r}.Sanitize()); err != nil {
				t.Error("fixture role cleanup failed")
			}
		})
	}
	if _, err = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{db}.Sanitize()+" OWNER "+pgx.Identifier{owner}.Sanitize()); err != nil {
		t.Fatal("fixture database creation failed")
	}
	t.Cleanup(func() {
		if _, err := admin.Exec(ctx, "DROP DATABASE "+pgx.Identifier{db}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error("fixture database cleanup failed")
		}
	})
	u.Path = "/" + db
	u.User = url.UserPassword(owner, password)
	ownerDSN := u.String()
	if err = instance.Migrate(ctx, ownerDSN, role); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, ownerDSN)
	if err != nil {
		t.Fatal("fixture owner unavailable")
	}
	t.Cleanup(pool.Close)
	u.User = url.UserPassword(role, password)
	return migrationFixture{pool, ownerDSN, u.String(), role}
}

func exec(t *testing.T, p *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, err := p.Exec(context.Background(), sql, args...); err != nil {
		t.Fatal(err)
	}
}

type relationState struct {
	OID                                          uint32
	Rows, Constraints, Indexes, Columns, History string
}

func state(t *testing.T, p *pgxpool.Pool, relation string) relationState {
	t.Helper()
	var out relationState
	ctx := context.Background()
	if err := p.QueryRow(ctx, `SELECT $1::regclass::oid`, relation).Scan(&out.OID); err != nil {
		t.Fatal(err)
	}
	if err := p.QueryRow(ctx, `SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY id)::text,'[]') FROM `+relation+` p`).Scan(&out.Rows); err != nil {
		t.Fatal(err)
	}
	if err := p.QueryRow(ctx, `SELECT jsonb_agg(jsonb_build_array(replace(conname,'compute_presets','presets'),contype,pg_get_constraintdef(oid),convalidated) ORDER BY replace(conname,'compute_presets','presets'))::text FROM pg_constraint WHERE conrelid=$1::regclass`, relation).Scan(&out.Constraints); err != nil {
		t.Fatal(err)
	}
	if err := p.QueryRow(ctx, `SELECT jsonb_agg(jsonb_build_array(replace(c.relname,'compute_presets','presets'),replace(replace(pg_get_indexdef(c.oid),'instance.compute_presets','compute.presets'),'compute_presets','presets'),i.indisvalid,i.indisready) ORDER BY replace(c.relname,'compute_presets','presets'))::text FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid=$1::regclass`, relation).Scan(&out.Indexes); err != nil {
		t.Fatal(err)
	}
	if err := p.QueryRow(ctx, `SELECT jsonb_agg(jsonb_build_array(a.attname,a.atttypid,a.attnotnull,pg_get_expr(d.adbin,d.adrelid),c.relowner) ORDER BY a.attnum)::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=$1::regclass AND a.attnum>0 AND NOT a.attisdropped`, relation).Scan(&out.Columns); err != nil {
		t.Fatal(err)
	}
	if err := p.QueryRow(ctx, `SELECT jsonb_agg(to_jsonb(v) ORDER BY version)::text FROM instance.schema_version v`).Scan(&out.History); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestComputeMigrationPreservesRelationAndRecovers(t *testing.T) {
	f := migrationDatabase(t)
	ctx := context.Background()
	gpu := map[string]any{"profile_id": uuid.NewString(), "profile_version": 1, "spec_digest": strings.Repeat("a", 64), "devices_per_instance": 1, "cluster_id": uuid.NewString(), "pool_id": uuid.NewString(), "mode": "SHARED_FIXED", "model_key": "T4", "memory_mib_per_device": 4096, "core_limit_percent": 25}
	raw, _ := json.Marshal(gpu)
	exec(t, f.owner, `INSERT INTO instance.compute_presets(id,code,name,compute_type,cpu_millis,memory_mib,enabled,gpu,created_by,updated_by) VALUES($1,'gpu-preserved','GPU','gpu',2000,4096,true,$2,'governance:user:42','governance:user:42')`, uuid.NewString(), raw)
	exec(t, f.owner, `INSERT INTO instance.compute_presets(id,code,name,compute_type,cpu_millis,memory_mib,enabled,created_by,updated_by) VALUES($1,'cpu-preserved','CPU','cpu',1000,1024,false,'governance:user:42','governance:user:42')`, uuid.NewString())
	before := state(t, f.owner, "instance.compute_presets")
	if err := compute.Migrate(ctx, f.ownerDSN, f.role); err != nil {
		t.Fatal(err)
	}
	after := state(t, f.owner, "compute.presets")
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("relation metadata/data/history changed: before=%+v after=%+v", before, after)
	}
	if err := compute.Migrate(ctx, f.ownerDSN, f.role); err != nil {
		t.Fatal("replay", err)
	}
	if got := state(t, f.owner, "compute.presets"); !reflect.DeepEqual(after, got) {
		t.Fatal("replay changed data/history")
	}
	// The explicit historical stage verifies every checksum, without replaying004.
	if err := instance.Migrate(ctx, f.ownerDSN, f.role); err != nil {
		t.Fatal("historical replay after move", err)
	}
	if got := state(t, f.owner, "compute.presets"); !reflect.DeepEqual(after, got) {
		t.Fatal("historical replay changed accepted data")
	}
	repo, err := compute.OpenPostgres(ctx, f.runtimeDSN)
	if err != nil {
		t.Fatal(err)
	}
	defer repo.Close()
	// Compute readiness is independent of instance migration versions.
	exec(t, f.owner, `UPDATE instance.schema_version SET checksum='changed' WHERE version=6`)
	if err = repo.CheckReady(ctx); err != nil {
		t.Fatal("compute readiness depends on instance", err)
	}
	if err = compute.Migrate(ctx, f.ownerDSN, f.role); err == nil {
		t.Fatal("unchecked historical checksum")
	}
	// Restore exact original history, without re-executing its SQL.
	var history []struct {
		Version  int    `json:"version"`
		Checksum string `json:"checksum"`
	}
	if err = json.Unmarshal([]byte(before.History), &history); err != nil {
		t.Fatal(err)
	}
	for _, v := range history {
		if v.Version == 6 {
			exec(t, f.owner, `UPDATE instance.schema_version SET checksum=$1 WHERE version=6`, v.Checksum)
		}
	}
	runtime, err := pgxpool.New(ctx, f.runtimeDSN)
	if err != nil {
		t.Fatal("runtime connection")
	}
	defer runtime.Close()
	tx, err := runtime.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	runtimeID := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO compute.presets(id,code,name,compute_type,cpu_millis,memory_mib,created_by,updated_by) VALUES($1,'runtime-write','runtime','cpu',1000,1024,'runtime','runtime')`, runtimeID); err != nil {
		t.Fatal("runtime insert denied", err)
	}
	if _, err = tx.Exec(ctx, `UPDATE compute.presets SET name='updated' WHERE id=$1`, runtimeID); err != nil {
		t.Fatal("runtime update denied", err)
	}
	if _, err = tx.Exec(ctx, `DELETE FROM compute.presets WHERE id=$1`, runtimeID); err != nil {
		t.Fatal("runtime delete denied", err)
	}
	if err = tx.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	for _, sql := range []string{`SELECT id FROM compute.presets`, `SELECT checksum FROM compute.schema_version`, `UPDATE compute.presets SET enabled=enabled WHERE false`, `DELETE FROM compute.presets WHERE false`} {
		if _, err = runtime.Exec(ctx, sql); err != nil {
			t.Fatal("runtime DML/read denied", err)
		}
	}
	for _, sql := range []string{`CREATE TABLE compute.forbidden(id int)`, `ALTER TABLE compute.presets ADD COLUMN forbidden int`, `UPDATE compute.schema_version SET checksum=checksum`, `DROP TABLE compute.presets`} {
		if _, err = runtime.Exec(ctx, sql); err == nil {
			t.Fatal("runtime DDL/metadata write allowed", sql)
		}
	}
	if err = compute.Restore(ctx, f.ownerDSN, f.role); err != nil {
		t.Fatal("restore", err)
	}
	if got := state(t, f.owner, "instance.compute_presets"); !reflect.DeepEqual(before, got) {
		t.Fatal("restore changed relation/data/history")
	}
	if err = repo.CheckReady(ctx); err == nil {
		t.Fatal("compute ready after restore")
	}
	if err = compute.Migrate(ctx, f.ownerDSN, f.role); err != nil {
		t.Fatal("forward after restore", err)
	}
	if got := state(t, f.owner, "compute.presets"); !reflect.DeepEqual(after, got) {
		t.Fatal("forward after restore changed data/history")
	}
}

func TestComputeMigrationFailureIsAtomic(t *testing.T) {
	for _, kind := range []string{"target-conflict", "checksum", "dependency", "lock-timeout"} {
		t.Run(kind, func(t *testing.T) {
			f := migrationDatabase(t)
			before := state(t, f.owner, "instance.compute_presets")
			var locked pgx.Tx
			switch kind {
			case "target-conflict":
				exec(t, f.owner, `CREATE SCHEMA compute; CREATE TABLE compute.presets(id uuid)`)
			case "checksum":
				exec(t, f.owner, `UPDATE instance.schema_version SET checksum='wrong' WHERE version=4`)
			case "dependency":
				exec(t, f.owner, `CREATE VIEW instance.unexpected AS SELECT id FROM instance.compute_presets`)
			case "lock-timeout":
				var err error
				locked, err = f.owner.Begin(context.Background())
				if err != nil {
					t.Fatal(err)
				}
				defer locked.Rollback(context.Background())
				if _, err = locked.Exec(context.Background(), `LOCK TABLE instance.compute_presets IN ACCESS EXCLUSIVE MODE`); err != nil {
					t.Fatal(err)
				}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
			defer cancel()
			if err := compute.Migrate(ctx, f.ownerDSN, f.role); err == nil {
				t.Fatal("expected explicit migration failure")
			}
			if locked != nil {
				if err := locked.Rollback(context.Background()); err != nil {
					t.Fatal(err)
				}
			}
			after := state(t, f.owner, "instance.compute_presets")
			if kind == "checksum" {
				before.History = after.History
			}
			if !reflect.DeepEqual(before, after) {
				t.Fatal("failed migration changed source relation")
			}
			var applied bool
			if err := f.owner.QueryRow(context.Background(), `SELECT to_regclass('compute.schema_version') IS NOT NULL`).Scan(&applied); err != nil || applied {
				t.Fatal("half-applied compute ledger", err)
			}
		})
	}
}
