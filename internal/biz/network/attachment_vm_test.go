package biz

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"testing"
	"time"
)

func vmSnapshotFixture() (Attachment, ConsumerSubmission) {
	a, c := managedSnapshotFixture()
	a.WorkloadMode = "vm_managed"
	c.ProtocolVersion = 3
	c.RootController = WorkloadReference{APIVersion: "kubevirt.io/v1", Kind: "VirtualMachine", Namespace: a.Namespace, Name: "vm", UID: "vm-uid"}
	vmi := WorkloadReference{APIVersion: "kubevirt.io/v1", Kind: "VirtualMachineInstance", Namespace: a.Namespace, Name: "vm", UID: "vmi-uid"}
	c.Controllers = []ControllerReference{{Reference: vmi, Owner: c.RootController}}
	c.CurrentPods[0].Owner = vmi
	return a, c
}

func TestVMConsumerRequiresFixedKubeVirtChain(t *testing.T) {
	for _, name := range []string{"valid", "v2", "notebook", "arbitrary-vmi", "wrong-root-owner", "wrong-pod-owner", "wrong-tenant", "two-current"} {
		t.Run(name, func(t *testing.T) {
			a, c := vmSnapshotFixture()
			switch name {
			case "v2":
				c.ProtocolVersion = 2
			case "notebook":
				c.RootController.Kind = "Notebook"
			case "arbitrary-vmi":
				c.Controllers[0].Reference.Kind = "StatefulSet"
			case "wrong-root-owner":
				c.Controllers[0].Owner.UID = "other"
			case "wrong-pod-owner":
				c.CurrentPods[0].Owner.UID = "other"
			case "wrong-tenant":
				c.TenantID = "33333333-3333-4333-8333-333333333333"
			case "two-current":
				c.CurrentPods = append(c.CurrentPods, c.CurrentPods[0])
			}
			if got := validConsumer(a, c); got != (name == "valid") {
				t.Fatal("VM consumer chain result", got)
			}
			legacy, _ := managedSnapshotFixture()
			if validConsumer(legacy, c) {
				t.Fatal("VM weakened Notebook v2 ownership")
			}
		})
	}
}

func TestVMConsumerRestartPreservesRootAndRetiredIdentities(t *testing.T) {
	a, old := vmSnapshotFixture()
	a.ConsumerSnapshot = &old
	a.RootController = old.RootController
	a.SnapshotRevision = old.SnapshotRevision
	a.SnapshotHash = ConsumerSnapshotHash(old)
	next := old
	next.SnapshotRevision++
	next.RetiredPods = append([]PodReference(nil), old.CurrentPods...)
	vmi := old.Controllers[0]
	vmi.Reference.UID = "vmi-next"
	next.Controllers = append(append([]ControllerReference(nil), old.Controllers...), vmi)
	pod := old.CurrentPods[0]
	pod.Reference.UID = "pod-next"
	pod.Owner = vmi.Reference
	next.CurrentPods = []PodReference{pod}
	if CompareConsumerSnapshot(a, next) != SnapshotAccepted {
		t.Fatal("legitimate native Restart rejected")
	}
	next.Controllers = next.Controllers[1:]
	if CompareConsumerSnapshot(a, next) != SnapshotConflict {
		t.Fatal("retired VMI identity forgotten")
	}
}

func TestVMClosedConsumerReleasesAfterNativeDependenciesExit(t *testing.T) {
	a, c := vmSnapshotFixture()
	now := time.Now()
	c.State = "closed"
	c.DesiredRunState = "deleted"
	c.FinalizationID = "33333333-3333-4333-8333-333333333333"
	c.ClosedAt = &now
	c.RetiredPods = c.CurrentPods
	c.CurrentPods = nil
	r := &managedWorkRepository{work: AttachmentWork{Attachment: a, Now: now}}
	w, err := NewAttachmentWorker(r, managedProvider{}, managedConsumer{c}, "44444444-4444-4444-8444-444444444444", DefaultWorkerPolicy())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = w.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if r.progress.State != Released || r.progress.FinalizationID != c.FinalizationID || r.progress.ProtocolBlocked {
		t.Fatal("closed consumer waited for its own Network release", r.progress)
	}
}

func TestVMBlockedClosedConsumerRequiresAcceptedSnapshotAndFreshAbsence(t *testing.T) {
	for _, name := range []string{"valid", "dependencies", "stale-proof", "new-revision", "changed-hash", "changed-finalization", "legacy-v2"} {
		t.Run(name, func(t *testing.T) {
			a, c := vmSnapshotFixture()
			now := time.Now()
			if name == "legacy-v2" {
				a, c = managedSnapshotFixture()
			}
			c.State, c.DesiredRunState = "closed", "deleted"
			c.FinalizationID = "33333333-3333-4333-8333-333333333333"
			c.ClosedAt = &now
			c.RetiredPods, c.CurrentPods = c.CurrentPods, nil
			a.State, a.FinalizationID, a.RootController = Releasing, c.FinalizationID, c.RootController
			a.SnapshotRevision, a.SnapshotHash = c.SnapshotRevision, ConsumerSnapshotHash(c)
			accepted := c
			a.ConsumerSnapshot = &accepted
			o := AttachmentObservation{Relations: []byte("[]"), Proof: ObservationProof{CollectedAt: now, CoveredGeneration: 1, Hash: "absence"}}
			switch name {
			case "dependencies":
				o.HasDependencies = true
			case "stale-proof":
				o.Proof.CollectedAt = now.Add(-time.Hour)
			case "new-revision":
				c.SnapshotRevision++
			case "changed-hash":
				c.ControllerUIDs = []string{"changed"}
			case "changed-finalization":
				c.FinalizationID = "55555555-5555-4555-8555-555555555555"
			}
			r := &managedWorkRepository{work: AttachmentWork{Attachment: a, Now: now, ProtocolBlocked: true, Relations: []byte("[]"), Requirement: ObservationRequirement{RequestedGeneration: 1, AppliedAt: now.Add(-time.Minute), Hash: "absence"}}}
			w, err := NewAttachmentWorker(r, managedProvider{value: o}, managedConsumer{c}, "44444444-4444-4444-8444-444444444444", DefaultWorkerPolicy())
			if err != nil {
				t.Fatal(err)
			}
			if _, err := w.Step(context.Background()); err != nil {
				t.Fatal(err)
			}
			if name == "valid" {
				if r.progress.State != Released || r.progress.ProtocolBlocked || !r.progress.ProtocolRecovered {
					t.Fatal("fresh independently empty accepted VM closure stayed fenced", r.progress)
				}
			} else if !r.progress.ProtocolBlocked || r.progress.ProtocolRecovered || r.progress.State == Released {
				t.Fatal("unverified closure cleared the protocol fence", r.progress)
			}
		})
	}
}

func TestVMExpectationDoesNotChangeLegacyPrepareFingerprint(t *testing.T) {
	r := PrepareAttachment{TenantID: "tenant", InstanceID: "instance", SubnetID: "subnet", VPCID: "vpc", Slot: "primary", RequestKey: "ignored", SubmissionID: "submission", Generation: 1, ClusterID: "cluster", Namespace: "namespace"}
	const intent = `"TenantID":"tenant","InstanceID":"instance","SubnetID":"subnet","VPCID":"vpc","Slot":"primary","RequestKey":"","SubmissionID":"submission","Generation":1,"ClusterID":"cluster","Namespace":"namespace"`
	for _, mode := range []string{"", "controller_managed"} {
		r.WorkloadMode = mode
		fields := intent
		if mode != "" {
			fields = `"WorkloadMode":"controller_managed",` + fields
		}
		sum := sha256.Sum256([]byte(`{"Version":1,"Intent":{` + fields + `}}`))
		if r.Fingerprint() != hex.EncodeToString(sum[:]) {
			t.Fatal("old canonical bytes changed", mode)
		}
	}
}
