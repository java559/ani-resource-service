package biz

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

type VirtualSwitchWorker struct {
	repository VirtualSwitchWorkRepository
	provider   VirtualSwitchGroupProvider
	owner      string
	policy     WorkerPolicy
}

func NewVirtualSwitchWorker(r VirtualSwitchWorkRepository, p VirtualSwitchGroupProvider, owner string, policy WorkerPolicy) (*VirtualSwitchWorker, error) {
	id, err := uuid.Parse(owner)
	if r == nil || p == nil || err != nil || id == uuid.Nil || policy.RequestTimeout <= 0 || policy.Lease < 3*policy.RequestTimeout || policy.ObserveEvery <= 0 {
		return nil, Fail(InvalidArgument, "invalid switch worker dependencies or policy")
	}
	return &VirtualSwitchWorker{r, p, id.String(), policy}, nil
}

func (w *VirtualSwitchWorker) Step(ctx context.Context) (worked bool, err error) {
	defer func() {
		if errors.Is(err, ErrLeaseLost) {
			err = nil
		}
	}()
	ctx, cancel := context.WithTimeout(ctx, w.policy.Lease)
	defer cancel()
	work, found, err := w.repository.ClaimVirtualSwitch(ctx, w.owner, w.policy.Lease)
	if err != nil || !found {
		return found, err
	}
	sw := work.Switch
	deleting := sw.State == Deleting
	finish := func(state string, reason Reason, delay time.Duration) (bool, error) {
		return true, w.repository.FinishVirtualSwitch(ctx, work, state, reason, delay)
	}
	for index, node := range sw.Nodes {
		call, cancel := context.WithTimeout(ctx, w.policy.RequestTimeout)
		current, inspectErr := w.provider.InspectVirtualSwitchNode(call, sw, node)
		cancel()
		if inspectErr != nil {
			state := "degraded"
			if deleting {
				state = "cleaning"
			}
			return finish(state, providerReason(inspectErr), w.policy.ObserveEvery)
		}
		if (!deleting && current.Applied) || (deleting && current.State == "cleaned") {
			if err = w.repository.SaveVirtualSwitchNode(ctx, work, index, current); err != nil {
				return true, err
			}
			continue
		}
		// The exact precondition and intended owner are durable before dispatch.
		current.Prepared = true
		current.Unknown = true
		if err = w.repository.SaveVirtualSwitchNode(ctx, work, index, current); err != nil {
			return true, err
		}
		call, cancel = context.WithTimeout(ctx, w.policy.RequestTimeout)
		applied, patchErr := w.provider.PatchVirtualSwitchNode(call, sw, current, deleting)
		cancel()
		if patchErr != nil {
			state := "degraded"
			if deleting {
				state = "cleaning"
			}
			return finish(state, providerReason(patchErr), w.policy.ObserveEvery)
		}
		if err = w.repository.SaveVirtualSwitchNode(ctx, work, index, applied); err != nil {
			return true, err
		}
		state := "pending"
		if deleting {
			state = "cleaning"
		}
		return finish(state, "", w.policy.RetryMin)
	}
	if deleting {
		return finish("cleaned", "", w.policy.ObserveEvery)
	}
	return finish("configured", "", w.policy.ObserveEvery)
}
