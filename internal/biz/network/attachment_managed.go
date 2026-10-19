package biz

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
)

type WorkloadReference struct{ APIVersion, Kind, Namespace, Name, UID string }
type ControllerReference struct{ Reference, Owner WorkloadReference }
type PodReference struct{ Reference, Owner WorkloadReference }
type SnapshotComparison int

const (
	SnapshotAccepted SnapshotComparison = iota
	SnapshotStale
	SnapshotConflict
)

func ConsumerSnapshotHash(c ConsumerSubmission) string {
	c.Controllers = append([]ControllerReference(nil), c.Controllers...)
	c.CurrentPods = append([]PodReference(nil), c.CurrentPods...)
	c.RetiredPods = append([]PodReference(nil), c.RetiredPods...)
	c.PodUIDs = append([]string(nil), c.PodUIDs...)
	c.ControllerUIDs = append([]string(nil), c.ControllerUIDs...)
	sort.Slice(c.Controllers, func(i, j int) bool { return c.Controllers[i].Reference.UID < c.Controllers[j].Reference.UID })
	sort.Slice(c.CurrentPods, func(i, j int) bool { return c.CurrentPods[i].Reference.UID < c.CurrentPods[j].Reference.UID })
	sort.Slice(c.RetiredPods, func(i, j int) bool { return c.RetiredPods[i].Reference.UID < c.RetiredPods[j].Reference.UID })
	sort.Strings(c.PodUIDs)
	sort.Strings(c.ControllerUIDs)
	b, _ := json.Marshal(c)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}
func referenceValid(r WorkloadReference, api, kind, namespace string) bool {
	return r.APIVersion == api && r.Kind == kind && r.Namespace == namespace && namespaceName.MatchString(r.Name) && r.UID != "" && len(r.UID) <= 128
}
func validManagedConsumer(a Attachment, c ConsumerSubmission) bool {
	if c.ProtocolVersion != 2 || c.SnapshotRevision < 1 || c.TenantID != a.TenantID || c.InstanceID != a.InstanceID || c.SubmissionID != a.SubmissionID || c.Generation != a.Generation || c.AttachmentID != a.ID || c.ClusterID != a.ClusterID || c.Namespace != a.Namespace {
		return false
	}
	if c.DesiredRunState != "running" && c.DesiredRunState != "stopped" && c.DesiredRunState != "deleted" {
		return false
	}
	if len(c.CurrentPods) > 1 || len(c.Controllers) > 4096 || len(c.RetiredPods) > 4096 {
		return false
	}
	if c.RootController.UID == "" {
		if c.RootController != (WorkloadReference{}) || len(c.Controllers) != 0 || len(c.CurrentPods) != 0 || len(c.RetiredPods) != 0 {
			return false
		}
	} else if !referenceValid(c.RootController, "kubeflow.org/v1", "Notebook", a.Namespace) {
		return false
	}
	sets := map[string]WorkloadReference{}
	for _, set := range c.Controllers {
		if !referenceValid(set.Reference, "apps/v1", "StatefulSet", a.Namespace) || set.Owner != c.RootController || sets[set.Reference.UID].UID != "" {
			return false
		}
		sets[set.Reference.UID] = set.Reference
	}
	seen := map[string]bool{}
	for _, group := range [][]PodReference{c.CurrentPods, c.RetiredPods} {
		for _, pod := range group {
			if !referenceValid(pod.Reference, "v1", "Pod", a.Namespace) || sets[pod.Owner.UID] != pod.Owner || seen[pod.Reference.UID] {
				return false
			}
			seen[pod.Reference.UID] = true
		}
	}
	if c.State != "open" && c.State != "closing" && c.State != "closed" {
		return false
	}
	if c.State == "open" {
		return c.FinalizationID == "" && c.ClosedAt == nil
	}
	if c.DesiredRunState != "deleted" || !UUIDFinalization(c.FinalizationID) {
		return false
	}
	return c.State != "closed" || (c.ClosedAt != nil && !c.ClosedAt.IsZero() && len(c.CurrentPods) == 0)
}
func UUIDFinalization(value string) bool {
	// Reuse release validation's canonical UUID boundary through ParseTenant.
	parsed, err := ParseTenant(value)
	return err == nil && parsed == value
}
func CompareConsumerSnapshot(a Attachment, c ConsumerSubmission) SnapshotComparison {
	if c.ProtocolVersion != 2 || c.TenantID != a.TenantID || c.InstanceID != a.InstanceID || c.SubmissionID != a.SubmissionID || c.Generation != a.Generation || c.AttachmentID != a.ID || c.ClusterID != a.ClusterID || c.Namespace != a.Namespace {
		return SnapshotConflict
	}
	if c.SnapshotRevision < a.SnapshotRevision {
		return SnapshotStale
	}
	if !validManagedConsumer(a, c) {
		return SnapshotConflict
	}
	if c.SnapshotRevision == a.SnapshotRevision && a.SnapshotHash != "" && ConsumerSnapshotHash(c) != a.SnapshotHash {
		return SnapshotConflict
	}
	if a.RootController.UID != "" && a.RootController != c.RootController {
		return SnapshotConflict
	}
	if a.FinalizationID != "" && a.FinalizationID != c.FinalizationID {
		return SnapshotConflict
	}
	if a.ConsumerSnapshot != nil {
		old := a.ConsumerSnapshot
		if old.FinalizationID != "" && old.FinalizationID != c.FinalizationID {
			return SnapshotConflict
		}
		rank := map[string]int{"open": 0, "closing": 1, "closed": 2}
		if rank[c.State] < rank[old.State] {
			return SnapshotConflict
		}
		known := map[string]bool{}
		for _, group := range [][]PodReference{c.CurrentPods, c.RetiredPods} {
			for _, pod := range group {
				known[pod.Reference.UID] = true
			}
		}
		for _, group := range [][]PodReference{old.CurrentPods, old.RetiredPods} {
			for _, pod := range group {
				if !known[pod.Reference.UID] {
					return SnapshotConflict
				}
			}
		}
	}
	return SnapshotAccepted
}

func (w *AttachmentWorker) stepManaged(ctx context.Context, work AttachmentWork, consumer ConsumerSubmission, consumerErr error) (bool, error) {
	a := work.Attachment
	p := AttachmentProgress{State: a.State, Reason: a.Reason, PodName: a.PodName, PodUID: a.PodUID, FinalizationID: a.FinalizationID, Relations: work.Relations, ProtocolBlocked: work.ProtocolBlocked, NextDelay: w.policy.ObserveEvery}
	accepted := a.ConsumerSnapshot
	if consumerErr == nil {
		switch CompareConsumerSnapshot(a, consumer) {
		case SnapshotAccepted:
			accepted = &consumer
			p.ConsumerSnapshot = accepted
		case SnapshotConflict:
			p.ProtocolBlocked = true
		case SnapshotStale:
			p.Backoff = true
		}
	} else {
		p.Backoff = true
	}
	work.ConsumerSnapshot = accepted
	if accepted != nil {
		for _, group := range [][]PodReference{accepted.CurrentPods, accepted.RetiredPods} {
			for _, pod := range group {
				work.ConsumerPodUIDs = append(work.ConsumerPodUIDs, pod.Reference.UID)
			}
		}
		work.RequireFreshRelations = accepted.State == "closed" && a.State != Released
	}
	call, cancel := context.WithTimeout(ctx, w.policy.RequestTimeout)
	o, observeErr := w.provider.ObserveAttachment(call, work)
	cancel()
	if observeErr != nil {
		p.Reason = providerReason(observeErr)
		p.Backoff = true
		if providerKind(observeErr) == ProviderConflict {
			p.ProtocolBlocked = true
		}
	} else {
		p.Observed = true
		p.Proof = o.Proof
		if p.Proof.CollectedAt.IsZero() {
			p.Proof = ObservationProof{CollectedAt: work.Now, CoveredGeneration: work.Requirement.RequestedGeneration}
		}
		p.Relations = o.Relations
		if a.State == Released && (o.Exists || o.HasDependencies) {
			p.ProtocolBlocked = true
		}
		// Address eligibility is renewed only by a verified current candidate.
		p.PodName = ""
		p.PodUID = ""
		if accepted == nil {
			p.Reason = ConsumerUnavailable
		} else if accepted.State == "closing" || accepted.State == "closed" {
			if a.State != Released {
				p.State = Releasing
				p.FinalizationID = accepted.FinalizationID
				p.Reason = ConsumerUnclosed
			}
			if accepted.State == "closed" && !o.Exists && !o.HasDependencies && !p.ProtocolBlocked {
				p.State = Released
				p.Reason = ""
			}
			if o.Exists || o.HasDependencies {
				p.Reason = CleanupPending
			}
		} else if a.State == Releasing || a.State == Released {
			p.Reason = ConsumerUnclosed
		} else if o.VerifiedCurrent && accepted.DesiredRunState == "running" {
			p.PodName = o.PodName
			p.PodUID = o.PodUID
			p.State = Attached
			p.Reason = ""
		} else {
			p.State = Reserved
			if o.PendingCandidate {
				p.Reason = ConsumerUnavailable
			} else if accepted.DesiredRunState == "running" {
				p.Reason = ProviderMissing
			} else {
				p.Reason = ""
			}
		}
	}
	if p.ProtocolBlocked {
		p.Reason = AttachmentProtocol
	}
	err := w.repository.FinishAttachment(ctx, work, p)
	if w.observer != nil {
		w.observer(ctx, work, p, err)
	}
	if errors.Is(err, ErrLeaseLost) {
		err = nil
	}
	return true, err
}
