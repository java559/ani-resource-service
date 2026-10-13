package server

import (
	"context"
	"log/slog"
	"sync"
	"time"

	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
)

// InstanceWorkerServer runs the instance lane in the Resource process, using
// only its own durable repository. It never adds instance kinds to Network.
type InstanceWorkerServer struct {
	worker *biz.Worker
	logger *slog.Logger
	mu     sync.Mutex
	cancel context.CancelFunc
	done   chan struct{}
}

func NewInstanceWorkerServer(w *biz.Worker, l *slog.Logger) *InstanceWorkerServer {
	return &InstanceWorkerServer{worker: w, logger: l, done: make(chan struct{})}
}
func (s *InstanceWorkerServer) Start(parent context.Context) error {
	s.mu.Lock()
	ctx, cancel := context.WithCancel(parent)
	s.cancel = cancel
	s.mu.Unlock()
	defer close(s.done)
	defer cancel()
	timer := time.NewTimer(0)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-timer.C:
		}
		worked, e := s.worker.Step(ctx)
		if ctx.Err() != nil {
			return nil
		}
		delay := time.Second
		if e != nil {
			s.logger.Warn("instance worker attempt failed")
			delay = 5 * time.Second
		} else if worked {
			delay = time.Millisecond
		}
		timer.Reset(delay)
	}
}
func (s *InstanceWorkerServer) Stop(ctx context.Context) error {
	s.mu.Lock()
	cancel := s.cancel
	s.mu.Unlock()
	if cancel == nil {
		return nil
	}
	cancel()
	select {
	case <-s.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
