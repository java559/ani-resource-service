package data

import (
	"fmt"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/tools/cache"
	"testing"
)

func TestNET05ARelationshipOldNewTombstoneAndUnrelatedEvents(t *testing.T) {
	o := &KCObservation{options: ObservationOptions{QueueCapacity: 16}, pending: map[string]struct{}{}, changes: map[string]uint64{}}
	old := &unstructured.Unstructured{Object: map[string]any{"kind": "Pod", "apiVersion": "v1", "metadata": map[string]any{"namespace": "ns", "name": "pod", "uid": "old", "labels": map[string]any{attachmentLabel: "a"}}}}
	next := old.DeepCopy()
	next.SetUID("new")
	next.SetLabels(map[string]string{attachmentLabel: "b"})
	v := &auditView{sequence: 0, epoch: 0}
	o.changed(old, next)
	for _, key := range []string{"attachment:a", "attachment:b", "uid:old", "uid:new"} {
		if _, ok := o.pending[key]; !ok {
			t.Fatalf("lost old/new side: %s", key)
		}
	}
	if o.valid(v, []string{"attachment:a"}) {
		t.Fatal("old LIST remained valid across related Watch")
	}
	if !o.valid(v, []string{"attachment:unrelated"}) {
		t.Fatal("unrelated event invalidated entire scope")
	}
	clear(o.pending)
	o.changed(cache.DeletedFinalStateUnknown{Key: "ns/pod", Obj: old}, nil)
	if _, ok := o.pending["uid:old"]; !ok {
		t.Fatal("tombstone identity lost")
	}
	before := o.sequence
	o.changed(next, next.DeepCopy())
	if o.sequence != before {
		t.Fatal("duplicate/resync became new fact")
	}
}

func TestNET05ASourceBoundaryInvalidatesOldAuditWithoutOrderingRV(t *testing.T) {
	o := &KCObservation{options: ObservationOptions{QueueCapacity: 16}, pending: map[string]struct{}{}, changes: map[string]uint64{}}
	old := &auditView{epoch: 0, sequence: 0}
	if !o.valid(old, []string{"uid:x"}) {
		t.Fatal("initial view unexpectedly invalid")
	}
	o.sourceBoundary()
	if o.valid(old, []string{"uid:x"}) || !o.overflow {
		t.Fatal("source reconnect preserved old audit proof")
	}
	current := &auditView{epoch: o.epoch, sequence: o.sequence}
	if !o.valid(current, []string{"uid:x"}) {
		t.Fatal("new complete scope cannot recover")
	}
}

func TestNET05AAttachmentInvalidationKeepsRelatedObjectsAndEpoch(t *testing.T) {
	newObserver := func() *KCObservation {
		return &KCObservation{options: ObservationOptions{QueueCapacity: 16}, pending: map[string]struct{}{}, changes: map[string]uint64{}}
	}
	sibling := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "Service", "metadata": map[string]any{"namespace": "ns", "name": "notebook", "uid": "service", "ownerReferences": []any{map[string]any{"apiVersion": "kubeflow.org/v1", "kind": "Notebook", "name": "notebook", "uid": "root"}}}}}
	observer := newObserver()
	view := &auditView{}
	observer.changed(nil, sibling)
	if observer.valid(view, []string{"uid:root"}) || !observer.validAttachment(view, []string{"uid:root"}) {
		t.Fatal("sibling Service changed Network scope or invalidated Attachment scope")
	}
	for _, tc := range []struct{ api, kind string }{{"v1", "Pod"}, {"networking.kubercloud.com/v1", "VNic"}, {"networking.kubercloud.com/v1", "VNicIP"}, {"networking.kubercloud.com/v1", "EIP"}} {
		t.Run(tc.kind, func(t *testing.T) {
			observer := newObserver()
			object := sibling.DeepCopy()
			object.SetAPIVersion(tc.api)
			object.SetKind(tc.kind)
			object.SetUID("old-related")
			next := object.DeepCopy()
			next.SetUID("new-related")
			observer.changed(object, next)
			for _, key := range []string{"uid:root", "uid:old-related", "uid:new-related"} {
				if observer.validAttachment(view, []string{key}) {
					t.Fatalf("related %s identity change retained old proof: %s", tc.kind, key)
				}
			}
			current := &auditView{sequence: observer.sequence, epoch: observer.epoch}
			observer.changed(cache.DeletedFinalStateUnknown{Key: "ns/notebook", Obj: next}, nil)
			if observer.validAttachment(current, []string{"uid:new-related"}) {
				t.Fatal("related tombstone retained old Attachment proof")
			}
		})
	}
	observer.sourceBoundary()
	if observer.validAttachment(view, []string{"uid:root"}) {
		t.Fatal("source continuity epoch was bypassed")
	}
	observer = newObserver()
	for i := 0; i < 130; i++ {
		object := sibling.DeepCopy()
		object.SetName(fmt.Sprintf("sibling-%d", i))
		object.SetUID(types.UID(fmt.Sprintf("sibling-%d", i)))
		observer.changed(nil, object)
	}
	if observer.epoch == 0 || observer.validAttachment(view, []string{"uid:root"}) {
		t.Fatal("bounded change overflow retained old Attachment proof")
	}
	if len(observer.attachmentChanges) > len(observer.changes) || len(observer.changes) > observer.options.QueueCapacity*8 {
		t.Fatal("Attachment changes escaped existing bounded change keys")
	}
}
