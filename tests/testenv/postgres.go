// Package testenv creates only task-owned PostgreSQL fixtures. It is imported by
// tests, never by normal runtime code.
package testenv

import (
	"context"
	"net/url"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/zhangzhe-ctrl/ani-resource-service/internal/data/network"
)

type Database struct {
	Repository                         *data.Postgres
	Owner                              *pgxpool.Pool
	RuntimeDSN, OwnerRole, RuntimeRole string
}

func NewDatabase(t *testing.T, beforeMigrate ...func(*pgxpool.Pool, string)) *Database {
	t.Helper()
	dsn := os.Getenv("NETWORK_TEST_ADMIN_DSN")
	if dsn == "" {
		t.Skip("requires scripts/integration's isolated PostgreSQL")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(admin.Close)
	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")
	dbName := "network_test_" + suffix
	ownerRole := "network_owner_" + suffix
	runtimeRole := "network_runtime_" + suffix
	address, err := url.Parse(dsn)
	if err != nil {
		t.Fatal("invalid test database address")
	}
	password, _ := address.User.Password()
	passwordLiteral := "'" + strings.ReplaceAll(password, "'", "''") + "'"
	for _, role := range []string{ownerRole, runtimeRole} {
		if _, err := admin.Exec(ctx, "CREATE ROLE "+pgx.Identifier{role}.Sanitize()+" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD "+passwordLiteral); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			if _, err := admin.Exec(ctx, "DROP ROLE "+pgx.Identifier{role}.Sanitize()); err != nil {
				t.Error(err)
			}
		})
	}
	if _, err := admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{dbName}.Sanitize()+" OWNER "+pgx.Identifier{ownerRole}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := admin.Exec(ctx, "DROP DATABASE "+pgx.Identifier{dbName}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
	})
	address.Path = "/" + dbName
	address.User = url.UserPassword(ownerRole, password)
	owner, err := pgxpool.New(ctx, address.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(owner.Close)
	for _, prepare := range beforeMigrate {
		prepare(owner, runtimeRole)
	}
	if err := data.Migrate(ctx, owner, runtimeRole); err != nil {
		t.Fatal(err)
	}
	// Migration replay and grant repair have a dedicated integration test;
	// ordinary fixtures need only the real initial migration.
	address.User = url.UserPassword(runtimeRole, password)
	repository, err := data.OpenPostgres(ctx, address.String(), data.Placement{ClusterID: "test-cluster", NamespacePrefix: "tenant-"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(repository.Close)
	return &Database{Repository: repository, Owner: owner, RuntimeDSN: address.String(), OwnerRole: ownerRole, RuntimeRole: runtimeRole}
}
