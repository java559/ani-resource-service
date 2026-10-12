//go:build imageintegration

package data_test

import (
	"context"
	"encoding/pem"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/image"
	imagedata "github.com/zhangzhe-ctrl/ani-resource-service/internal/data/image"
)

type spaceLockBarrierRepository struct {
	biz.LifecycleRepository
	entered chan struct{}
	release chan struct{}
}

func (r *spaceLockBarrierRepository) WithSpaceWriteLock(ctx context.Context, space string, fn func(context.Context) error) error {
	return r.LifecycleRepository.WithSpaceWriteLock(ctx, space, func(locked context.Context) error {
		r.entered <- struct{}{}
		select {
		case <-r.release:
			return fn(locked)
		case <-ctx.Done():
			return ctx.Err()
		}
	})
}

func assertNoSpaceLocks(t *testing.T, f *fixture) {
	t.Helper()
	var locks int
	err := f.Runtime.QueryRow(context.Background(), `SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND a.usename=current_user`).Scan(&locks)
	if err != nil || locks != 0 {
		t.Fatalf("residual advisory locks=%d err=%v", locks, err)
	}
}

func TestSpaceLockEightSpacesLifecycleProgress(t *testing.T) {
	f, registry, cfg, ring, _ := lifecycleFixture(t)
	const writers = 8 // Equal to the existing connection budget, not a larger pool.
	barrier := &spaceLockBarrierRepository{LifecycleRepository: f.Repo, entered: make(chan struct{}, writers), release: make(chan struct{})}
	l := lifecycle(t, barrier, registry, ring, cfg, nil)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	results := make(chan error, writers)
	var wg sync.WaitGroup
	for i := range writers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			tenant := uuid.NewString()
			caller := biz.WithCaller(ctx, biz.Caller{Kind: biz.GovernanceCaller, Subject: "governance", Actor: "user:test", TenantID: tenant})
			input := biz.EnableSpace{TenantID: tenant, Slug: fmt.Sprintf("eight-%02d", i), IdempotencyKey: "eight-enable"}
			space, err := l.EnsureImageSpace(caller, input)
			if err == nil && (space.State != "available" || space.ProjectID <= 0) {
				err = fmt.Errorf("space did not become available")
			}
			if err == nil {
				// A fresh use case verifies durable replay after the first write.
				plain, e := biz.NewLifecycle(f.Repo, registry, ring, cfg, nil)
				if e == nil {
					_, e = plain.EnsureImageSpace(caller, input)
				}
				err = e
			}
			results <- err
		}()
	}
	entered := 0
	for entered < writers && ctx.Err() == nil {
		select {
		case <-barrier.entered:
			entered++
		case <-ctx.Done():
		}
	}
	// No query in any callback can run until all eight dedicated lock
	// connections are held. The old pool cannot then supply a ninth connection.
	close(barrier.release)
	wg.Wait()
	close(results)
	success := 0
	for err := range results {
		if err == nil {
			success++
		} else {
			t.Logf("writer result: %v", err)
		}
	}
	assertNoSpaceLocks(t, f)
	registry.mu.Lock()
	creates, robots, sets := registry.creates, registry.robotCreates, registry.secretSets
	registry.mu.Unlock()
	if entered != writers || success != writers || creates != writers || robots != writers || sets != writers {
		t.Fatalf("eight-space progress: entered=%d succeeded=%d projects=%d robots=%d secrets=%d", entered, success, creates, robots, sets)
	}
	var connections int
	if err := f.Runtime.QueryRow(context.Background(), `SELECT count(*) FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid()`).Scan(&connections); err != nil || connections > writers {
		t.Fatalf("connection budget exceeded: %d %v", connections, err)
	}
	// The fixture's observer and repository use independent pools with the same
	// runtime role. Close the observer too, and inspect from the distinct owner
	// role so none of the observer's own connections count as repository leaks.
	f.Runtime.Close()
	closed := make(chan struct{})
	go func() {
		f.Repo.Close()
		close(closed)
	}()
	select {
	case <-closed:
	case <-time.After(5 * time.Second):
		t.Fatal("repository Close did not return after all space writers completed")
	}
	// pgx closes the client sockets before PostgreSQL necessarily removes their
	// backends. Observe that asynchronous cleanup within one bounded assertion;
	// every connection and advisory lock for this fixture's role must disappear.
	cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cleanupCancel()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	var locks int
	var backendPIDs []int32
	for {
		err := f.Owner.QueryRow(cleanupCtx, `WITH runtime_connections AS (
 SELECT pid FROM pg_stat_activity WHERE usename=$1
)
SELECT count(*), ARRAY(SELECT pid FROM runtime_connections ORDER BY pid),
 (SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid IN (SELECT pid FROM runtime_connections))
FROM runtime_connections`, f.RuntimeRole).Scan(&connections, &backendPIDs, &locks)
		if err != nil {
			t.Fatalf("inspect repository cleanup: connections=%d backend_pids=%v advisory_locks=%d err=%v", connections, backendPIDs, locks, err)
		}
		if connections == 0 && locks == 0 {
			break
		}
		select {
		case <-ticker.C:
		case <-cleanupCtx.Done():
			t.Fatalf("repository cleanup deadline: connections=%d backend_pids=%v advisory_locks=%d", connections, backendPIDs, locks)
		}
	}
}

func TestSpaceLockCallbackFailureAndCancellation(t *testing.T) {
	for _, scenario := range []string{"callback_error", "request_cancel", "connection_lost"} {
		t.Run(scenario, func(t *testing.T) {
			f := database(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			space := reserve(t, f, uuid.NewString(), "t-lock-failure")
			failure := errors.New("injected callback failure")
			err := f.Repo.WithSpaceWriteLock(ctx, space.ID, func(locked context.Context) error {
				if _, err := f.Repo.FindTenantSpace(locked, space.TenantID); err != nil {
					return err
				}
				switch scenario {
				case "callback_error":
					return failure
				case "request_cancel":
					cancel()
					return locked.Err()
				case "connection_lost":
					// The runtime role is unique to this fixture. Terminate only
					// its one backend currently holding this test's advisory lock.
					var terminated bool
					return f.Runtime.QueryRow(context.Background(), `SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a WHERE a.usename=current_user AND a.pid<>pg_backend_pid() AND EXISTS(SELECT 1 FROM pg_locks l WHERE l.pid=a.pid AND l.locktype='advisory' AND l.granted)`).Scan(&terminated)
				}
				return nil
			})
			if err == nil || (scenario == "callback_error" && !errors.Is(err, failure)) || (scenario == "request_cancel" && !errors.Is(err, context.Canceled)) {
				t.Fatal("failure was swallowed", err)
			}
			assertNoSpaceLocks(t, f)
			if err = f.Repo.WithSpaceWriteLock(context.Background(), space.ID, func(locked context.Context) error {
				_, err := f.Repo.FindTenantSpace(locked, space.TenantID)
				return err
			}); err != nil {
				t.Fatal("follow-up operation could not progress", err)
			}
			assertNoSpaceLocks(t, f)
		})
	}
}

func TestSpaceLockNoTransactionDuringHarbor(t *testing.T) {
	f, registry, cfg, ring, ctx := lifecycleFixture(t)
	upstream := registryHTTP(t, registry)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var transactions int
		if err := f.Runtime.QueryRow(context.Background(), `SELECT count(*) FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND xact_start IS NOT NULL`).Scan(&transactions); err != nil || transactions != 0 {
			t.Errorf("SQL transaction held across Harbor request: count=%d err=%v", transactions, err)
		}
		upstream.Config.Handler.ServeHTTP(w, r)
	}))
	t.Cleanup(server.Close)
	h, err := imagedata.NewHarbor(imagedata.HarborConfig{URL: server.URL, Username: "fixture-admin", Password: biz.Secret("fixture-password"), RobotNamePrefix: "fixture$", CAPEM: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw})})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(h.Close)
	l := lifecycle(t, f.Repo, h, ring, cfg, nil)
	tenant := tenantFrom(t, ctx)
	if _, err = l.EnsureImageSpace(ctx, biz.EnableSpace{TenantID: tenant, Slug: "transaction", IdempotencyKey: "transaction-enable"}); err != nil {
		t.Fatal(err)
	}
	issued, err := l.IssuePublisherCredential(ctx, biz.IssueCredential{TenantID: tenant, IdempotencyKey: "transaction-issue"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = l.DisablePublisherCredential(ctx, biz.DisableCredential{TenantID: tenant, IdempotencyKey: "transaction-disable", ExpectedVersion: issued.Credential.Version}); err != nil {
		t.Fatal(err)
	}
	assertNoSpaceLocks(t, f)
}
