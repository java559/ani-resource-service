package server

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	storage "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/storage"
)

// StorageDeleteWorker joins the normal process lifecycle. Cancellation waits
// for the current bounded step before the owner repository is closed.
type StorageDeleteWorker struct {
	usecase *storage.UseCase
	logger  *slog.Logger
	running atomic.Bool
	mu      sync.Mutex
	// cancel and done are published per round under mu. Stop consumes the
	// current round's cancel and joins on that same round's done, so a later
	// Start can open a fresh round without racing the previous one.
	cancel context.CancelFunc
	done   chan struct{}
}

func NewStorageDeleteWorker(u *storage.UseCase, logger *slog.Logger) *StorageDeleteWorker {
	return &StorageDeleteWorker{usecase: u, logger: logger}
}

// Ready reports whether a started round is still inside its loop. It is set
// once at entry and cleared when Stop begins; per-step errors never flip it.
func (s *StorageDeleteWorker) Ready() bool { return s.running.Load() }

func (s *StorageDeleteWorker) Start(parent context.Context) error {
	ctx, cancel := context.WithCancel(parent)
	s.mu.Lock()
	// Publish cancel and running in one critical section. Only a round whose
	// cancel has not been consumed by Stop may enter the loop, so a stale
	// round can never re-raise Ready after Stop cleared it.
	if s.cancel != nil {
		s.mu.Unlock()
		cancel()
		return nil
	}
	s.cancel = cancel
	s.done = make(chan struct{})
	done := s.done
	s.running.Store(true)
	s.mu.Unlock()
	defer close(done)
	defer s.running.Store(false)
	defer cancel()
	if s.logger != nil {
		s.logger.Info("storage PVC delete recovery worker started")
	}
	timer := time.NewTimer(0)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			if s.logger != nil {
				s.logger.Info("storage PVC delete recovery worker stopped")
			}
			return nil
		case <-timer.C:
		}
		// Step failures are logged with their real causes at the concrete
		// sites in delete_recovery; the loop itself adds no generic WARN.
		_, _ = s.usecase.StepDeleteRecovery(ctx)
		timer.Reset(5 * time.Second)
	}
}

func (s *StorageDeleteWorker) Stop(ctx context.Context) error {
	s.mu.Lock()
	cancel := s.cancel
	done := s.done
	s.cancel = nil
	s.done = nil
	if cancel != nil {
		s.running.Store(false)
	}
	s.mu.Unlock()
	if cancel == nil {
		// Stop before any Start is a legal no-op; a later Start stays free to
		// enter the loop and become ready.
		return nil
	}
	cancel()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
