package instance

import (
	"context"
	"errors"
	"time"
)

type Worker struct {
	repo     Repository
	provider Provider
	quota    QuotaReporter
	owner    string
}

func NewWorker(r Repository, p Provider, q QuotaReporter, owner string) (*Worker, error) {
	if r == nil || p == nil || q == nil || owner == "" {
		return nil, ErrInvalid
	}
	return &Worker{r, p, q, owner}, nil
}
func (w *Worker) Step(ctx context.Context) (bool, error) {
	work, err := w.repo.Claim(ctx, w.owner, 45*time.Second)
	if err != nil || work == nil {
		return false, err
	}
	i := work.Instance
	p := Progress{Instance: i, Next: 5 * time.Second}
	var obs Observation
	// A DELETE-first tombstone has never accepted an external creation. Its
	// admission lock prevents any later CREATE from enabling external writes.
	if i.Spec.NotebookName != "" {
		obs, err = w.provider.Observe(ctx, i)
	}
	if err != nil {
		p.Instance.Reason = "PROVIDER_OBSERVATION_FAILED"
		return true, w.repo.Finish(ctx, *work, p)
	}
	if obs.NotebookExists {
		p.Instance.NotebookUID = obs.NotebookUID
		p.Instance.NotebookRV = obs.NotebookRV
	}
	if obs.PVCExists {
		p.Instance.PVCUID = obs.PVCUID
	}
	if obs.StatefulSetUID != "" {
		p.Instance.StatefulSetUID = obs.StatefulSetUID
	}
	if obs.VolumeName != "" {
		p.Instance.VolumeName = obs.VolumeName
		p.Instance.VolumeUID = obs.VolumeUID
	}
	p.Instance.PodUID = obs.PodUID
	p.Instance.PodName = obs.PodName
	if i.Pending != nil {
		switch i.Pending.Kind {
		case "create_notebook":
			if !obs.NotebookExists {
				p.Instance.Reason = "CREATE_RESULT_UNKNOWN"
				return true, w.repo.Finish(ctx, *work, p)
			}
		case "create_workspace":
			if !obs.PVCExists {
				p.Instance.Reason = "WORKSPACE_CREATE_RESULT_UNKNOWN"
				return true, w.repo.Finish(ctx, *work, p)
			}
		// Conditional updates and UID-preconditioned deletes can safely be re-observed.
		case "set_stopped":
			if !obs.NotebookExists {
				p.Instance.Reason = "NOTEBOOK_MISSING"
				return true, w.repo.Finish(ctx, *work, p)
			}
		}
		p.Instance.Pending = nil
		p.Receipt = &MutationReceipt{Mutation: *i.Pending, Outcome: "recovered_by_observation"}
		p.Instance.Reason = ""
		// Persist recovered evidence before any next mutation.
		return true, w.repo.Finish(ctx, *work, p)
	}
	mutate := func(kind string, call func() error) (bool, error) {
		m := Mutation{Kind: kind, OperationID: i.LastOperationID, Version: i.Version, TargetUID: obs.NotebookUID, ResourceVersion: obs.NotebookRV}
		if kind == "delete_workspace" {
			m.TargetUID = obs.PVCUID
		}
		if err := w.repo.BeginMutation(ctx, *work, m); err != nil {
			if errors.Is(err, ErrVersion) || errors.Is(err, ErrLease) {
				return true, nil
			}
			return true, err
		}
		p.Instance.Pending = &m
		err := call()
		if err == nil {
			p.Instance.Pending = nil
			p.Instance.Reason = ""
			p.Receipt = &MutationReceipt{Mutation: m, Outcome: "succeeded"}
		} else if errors.Is(err, ErrUnknown) {
			p.Instance.Reason = "EXTERNAL_RESULT_UNKNOWN"
		} else {
			p.Instance.Pending = nil
			p.Instance.Reason = "PROVIDER_MUTATION_FAILED"
			p.Receipt = &MutationReceipt{Mutation: m, Outcome: "definitive_rejection"}
		}
		return true, w.repo.Finish(ctx, *work, p)
	}
	if i.Desired == Deleted {
		if obs.NotebookExists {
			return mutate("delete_notebook", func() error { return w.provider.DeleteNotebook(ctx, p.Instance, obs) })
		}
		if obs.PodExists {
			p.Instance.Reason = "WAITING_FOR_COMPUTE_CLEANUP"
			return true, w.repo.Finish(ctx, *work, p)
		}
		p.Instance.ComputeCleaned = true
		if !i.ComputeCleaned {
			return true, w.repo.Finish(ctx, *work, p)
		}
		// A stable event is rebuilt from original CREATE/charges; PVC errors do not hold GPU quota.
		if !i.QuotaReturned {
			if err := w.quota.Report(ctx, p.Instance); err != nil {
				p.Instance.Reason = "QUOTA_RETURN_PENDING"
			} else {
				p.Instance.QuotaReturned = true
			}
			return true, w.repo.Finish(ctx, *work, p)
		}
		if obs.PVCExists {
			if obs.WorkspaceReason != "" {
				p.Instance.Reason = obs.WorkspaceReason
				return true, w.repo.Finish(ctx, *work, p)
			}
			return mutate("delete_workspace", func() error { return w.provider.DeleteWorkspace(ctx, p.Instance, obs) })
		}
		if obs.WorkspaceReason != "" {
			p.Instance.Reason = obs.WorkspaceReason
			return true, w.repo.Finish(ctx, *work, p)
		}
		if obs.VolumeExists {
			p.Instance.Reason = "WAITING_FOR_WORKSPACE_RECLAIM"
			return true, w.repo.Finish(ctx, *work, p)
		}
		p.Instance.WorkspaceCleaned = true
		p.Instance.Observed = Deleted
		p.Instance.Reason = ""
		p.Next = 0
		return true, w.repo.Finish(ctx, *work, p)
	}
	if i.Desired == Stopped && obs.NotebookExists {
		if !obs.Stopped || obs.IntentVersion != i.Version {
			return mutate("set_stopped", func() error { return w.provider.SetStopped(ctx, p.Instance, obs, true) })
		}
		if !obs.PodExists {
			p.Instance.Observed = Stopped
			p.Instance.Reason = ""
			p.Next = 30 * time.Second
			return true, w.repo.Finish(ctx, *work, p)
		}
	}
	if obs.WorkspaceReason != "" {
		p.Instance.Reason = obs.WorkspaceReason
		return true, w.repo.Finish(ctx, *work, p)
	}
	if !obs.PVCExists {
		if i.PVCUID != "" {
			p.Instance.Reason = "WORKSPACE_MISSING"
			return true, w.repo.Finish(ctx, *work, p)
		}
		return mutate("create_workspace", func() error { uid, e := w.provider.EnsureWorkspace(ctx, p.Instance); p.Instance.PVCUID = uid; return e })
	}
	if !obs.NotebookExists {
		// A recorded UID proves a Notebook previously existed. Losing it is not a new CREATE.
		if i.NotebookUID != "" {
			p.Instance.Reason = "NOTEBOOK_MISSING"
			return true, w.repo.Finish(ctx, *work, p)
		}
		return mutate("create_notebook", func() error {
			uid, e := w.provider.CreateNotebook(ctx, p.Instance)
			p.Instance.NotebookUID = uid
			return e
		})
	}
	stopped := i.Desired == Stopped
	if obs.Stopped != stopped || obs.IntentVersion != i.Version {
		return mutate("set_stopped", func() error { return w.provider.SetStopped(ctx, p.Instance, obs, stopped) })
	}
	if stopped && !obs.PodExists {
		p.Instance.Observed = Stopped
		p.Instance.Reason = ""
		p.Next = 30 * time.Second
	} else if !stopped && obs.PodReady {
		p.Instance.Observed = Running
		p.Instance.Reason = ""
		p.Next = 30 * time.Second
	} else {
		p.Instance.Observed = "pending"
		p.Instance.Reason = "WAITING_FOR_NOTEBOOK"
	}
	return true, w.repo.Finish(ctx, *work, p)
}
