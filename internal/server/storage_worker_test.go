package server

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	storage "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/storage"
)

var errUnavailableVolumes = errors.New("volume port unavailable")

// unavailableVolumes fails every call; with the guard unconfigured the worker
// never reaches Kubernetes, so each step fails fast at the guard precondition.
type unavailableVolumes struct{}

func (unavailableVolumes) GetNamespace(context.Context, string) (storage.Namespace, error) {
	return storage.Namespace{}, errUnavailableVolumes
}

func (unavailableVolumes) GetVolume(context.Context, string, string) (*storage.VolumeRecord, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) CreateVolume(context.Context, string, storage.CreateSpec) (*storage.VolumeRecord, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) ListVolumes(context.Context, string, string) ([]*storage.VolumeRecord, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) ResizeVolume(context.Context, string, string, string, string, string, int64) (*storage.VolumeRecord, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) DeleteVolume(context.Context, string, string, string) error {
	return errUnavailableVolumes
}

func (unavailableVolumes) ListStorageClasses(context.Context) ([]storage.Class, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) GetStorageClass(context.Context, string) (*storage.Class, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) GetPersistentVolume(context.Context, string) (*storage.PVRecord, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) ListPodConsumers(context.Context, string, string) ([]storage.Consumer, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) ListNotebookConsumers(context.Context, string, string) ([]storage.Consumer, error) {
	return nil, errUnavailableVolumes
}

func (unavailableVolumes) ListEvents(context.Context, string, string) ([]storage.Event, error) {
	return nil, errUnavailableVolumes
}

// lockedBuffer is safe for the log handler goroutine and the asserting test.
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func newWorkerFixture(t *testing.T) (*StorageDeleteWorker, *lockedBuffer) {
	t.Helper()
	out := &lockedBuffer{}
	logger := slog.New(slog.NewJSONHandler(out, nil))
	usecase, err := storage.NewUseCase(unavailableVolumes{}, nil, storage.Config{
		NamespacePrefix: "tenant-",
		ClusterID:       "11111111-1111-4111-8111-111111111111",
		Logger:          logger,
	})
	if err != nil {
		t.Fatal(err)
	}
	return NewStorageDeleteWorker(usecase, logger), out
}

func waitReady(t *testing.T, w *StorageDeleteWorker) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if w.Ready() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("worker did not become ready")
}

// ST01: Ready is false before Start, true once the round enters its loop and
// false again once Stop begins; Start returns after Stop joined the round.
func TestStorageDeleteWorkerReadyLifecycle(t *testing.T) {
	w, _ := newWorkerFixture(t)
	if w.Ready() {
		t.Fatal("ready before start")
	}
	ctx := context.Background()
	returned := make(chan error, 1)
	go func() { returned <- w.Start(ctx) }()
	waitReady(t, w)
	if err := w.Stop(ctx); err != nil {
		t.Fatal("stop did not join the round", err)
	}
	if w.Ready() {
		t.Fatal("ready after stop")
	}
	select {
	case err := <-returned:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("start did not return after stop")
	}
}

// ST01: Stop before any Start holds no cancel, so it is a legal no-op and a
// later Start still enters the loop and becomes ready.
func TestStorageDeleteWorkerStopBeforeStartIsNoOp(t *testing.T) {
	w, _ := newWorkerFixture(t)
	ctx := context.Background()
	if err := w.Stop(ctx); err != nil {
		t.Fatal("stop before start must be a legal no-op", err)
	}
	returned := make(chan error, 1)
	go func() { returned <- w.Start(ctx) }()
	waitReady(t, w)
	if err := w.Stop(ctx); err != nil {
		t.Fatal(err)
	}
	if w.Ready() {
		t.Fatal("later start did not stop cleanly")
	}
	<-returned
}

// ST01: a second Start during a live round is a no-op; it must not spawn a
// competing loop or disturb the running round.
func TestStorageDeleteWorkerSecondStartIsNoOp(t *testing.T) {
	w, _ := newWorkerFixture(t)
	ctx := context.Background()
	returned := make(chan error, 1)
	go func() { returned <- w.Start(ctx) }()
	waitReady(t, w)
	if err := w.Start(ctx); err != nil {
		t.Fatal("second start must be a no-op", err)
	}
	if !w.Ready() {
		t.Fatal("second start disturbed the running round")
	}
	if err := w.Stop(ctx); err != nil || w.Ready() {
		t.Fatal("stop after the second start did not clear ready", err)
	}
	<-returned
	if err := w.Stop(ctx); err != nil {
		t.Fatal("stop after the round joined must stay a no-op", err)
	}
}

// ST01: under concurrent observation, Ready never rises again after Stop
// cleared it for this round — there is no observable truth window.
func TestStorageDeleteWorkerReadyNeverRisesAfterStopTookCancel(t *testing.T) {
	w, _ := newWorkerFixture(t)
	ctx := context.Background()
	returned := make(chan error, 1)
	go func() { returned <- w.Start(ctx) }()
	waitReady(t, w)
	var (
		mu       sync.Mutex
		samples  []bool
		stopPoll = make(chan struct{})
	)
	go func() {
		for {
			select {
			case <-stopPoll:
				return
			default:
			}
			mu.Lock()
			samples = append(samples, w.Ready())
			mu.Unlock()
			time.Sleep(50 * time.Microsecond)
		}
	}()
	time.Sleep(5 * time.Millisecond)
	if err := w.Stop(ctx); err != nil {
		t.Fatal(err)
	}
	if w.Ready() {
		t.Fatal("ready must be cleared once stop has taken the cancel")
	}
	close(stopPoll)
	<-returned
	// Deterministic post-stop burst: once cleared for this round, Ready never
	// rises again (the Windows timer granularity makes fine-grained sampling
	// unreliable, so the no-re-raise invariant is asserted directly here and
	// only the no-true-after-false history is checked against the samples).
	for i := 0; i < 500; i++ {
		if w.Ready() {
			t.Fatal("ready rose again after stop cleared it for this round")
		}
	}
	mu.Lock()
	defer mu.Unlock()
	phase := 0 // 0 before start observed, 1 running, 2 stopped
	for _, ready := range samples {
		switch {
		case ready && phase < 1:
			phase = 1
		case !ready && phase == 1:
			phase = 2
		case ready && phase == 2:
			t.Fatal("ready rose again after stop cleared it for this round")
		}
	}
}

// ST02: step failures surface at the concrete log site with their structured
// fields, the loop survives them with Ready still true, and the old generic
// per-loop WARN is gone.
func TestStorageDeleteWorkerStepErrorsLoggedAtSiteAndLoopSurvives(t *testing.T) {
	w, out := newWorkerFixture(t)
	ctx := context.Background()
	returned := make(chan error, 1)
	go func() { returned <- w.Start(ctx) }()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && !strings.Contains(out.String(), "storage PVC delete recovery guard unavailable") {
		time.Sleep(time.Millisecond)
	}
	logs := out.String()
	if !strings.Contains(logs, "storage PVC delete recovery guard unavailable") {
		t.Fatal("step failure was not logged at the concrete site")
	}
	if !strings.Contains(logs, `"operation":"storage.delete_recovery"`) || !strings.Contains(logs, `"reason":"guard_not_configured"`) {
		t.Fatal("structured fields missing from the step failure log", logs)
	}
	if strings.Contains(logs, "storage PVC delete recovery unavailable") {
		t.Fatal("the old generic per-loop WARN reappeared")
	}
	if !w.Ready() {
		t.Fatal("worker loop did not survive the step error")
	}
	if err := w.Stop(ctx); err != nil {
		t.Fatal(err)
	}
	<-returned
}
