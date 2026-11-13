package server

import (
	"context"
	"testing"
	"time"

	storagev1 "github.com/zhangzhe-ctrl/ani-resource-service/api/storage/v1"
	storage "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/storage"
	storageservice "github.com/zhangzhe-ctrl/ani-resource-service/internal/service/storage"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

const (
	detailTenant = "11111111-1111-4111-8111-111111111111"
	detailNS     = "tenant-11111111-1111-4111-8111-111111111111"
)

// detailStubVolumes serves a fast main read plus one cancellation-aware
// auxiliary dependency, standing in for the real dynamic client.
type detailStubVolumes struct {
	unavailableVolumes
	pods        []storage.Consumer
	notebookErr error
	blockEvents bool
	eventErr    error
	record      *storage.VolumeRecord
}

func (f *detailStubVolumes) GetNamespace(context.Context, string) (storage.Namespace, error) {
	return storage.Namespace{UID: "namespace-uid", Phase: "Active", Name: detailNS, ManagedBy: storage.NamespaceOwner, TenantID: detailTenant}, nil
}

func (f *detailStubVolumes) GetVolume(context.Context, string, string) (*storage.VolumeRecord, error) {
	if f.record == nil {
		return nil, storage.ErrNotFound
	}
	record := *f.record
	return &record, nil
}

func (f *detailStubVolumes) ListPodConsumers(context.Context, string, string) ([]storage.Consumer, error) {
	return f.pods, nil
}

func (f *detailStubVolumes) ListNotebookConsumers(context.Context, string, string) ([]storage.Consumer, error) {
	return nil, f.notebookErr
}

func (f *detailStubVolumes) ListEvents(ctx context.Context, _ string, _ string) ([]storage.Event, error) {
	if f.blockEvents {
		<-ctx.Done()
		return nil, ctx.Err()
	}
	return nil, f.eventErr
}

func (f *detailStubVolumes) GetPersistentVolume(context.Context, string) (*storage.PVRecord, error) {
	return &storage.PVRecord{Name: "pv-1", ReclaimPolicy: "Delete"}, nil
}

func (f *detailStubVolumes) GetStorageClass(context.Context, string) (*storage.Class, error) {
	return &storage.Class{Name: "fast", ReclaimPolicy: "Delete"}, nil
}

func detailStubUseCase(t *testing.T, stub *detailStubVolumes) *storageservice.Service {
	t.Helper()
	usecase, err := storage.NewUseCase(stub, nil, storage.Config{NamespacePrefix: "tenant-", ClusterID: detailTenant})
	if err != nil {
		t.Fatal(err)
	}
	svc, err := storageservice.NewService(usecase)
	if err != nil {
		t.Fatal(err)
	}
	return svc
}

func detailRecord() *storage.VolumeRecord {
	return &storage.VolumeRecord{
		Name: "data", UID: "pvc-uid-1", PVName: "pv-1", StorageClassName: "fast",
		Labels: map[string]string{storage.LabelManagedBy: storage.ManagedByValue, storage.LabelTenantID: detailTenant},
	}
}

// ST09: with the real 1s unary timeout middleware in front, a fast PVC main
// read plus one blocked auxiliary dependency still returns the main detail;
// only the blocked group degrades to known=false with its fixed code.
func TestVolumeDetailUnderOneSecondUnaryTimeoutKeepsMainDetail(t *testing.T) {
	stub := &detailStubVolumes{record: detailRecord(), pods: []storage.Consumer{{Kind: "Pod", Namespace: detailNS, Name: "worker", Claim: "data"}}, blockEvents: true}
	svc := detailStubUseCase(t, stub)
	handler := unaryRequestTimeout(time.Second)(func(ctx context.Context, req any) (any, error) {
		return svc.GetVolume(ctx, req.(*storagev1.GetVolumeRequest))
	})
	request := &storagev1.GetVolumeRequest{TenantId: detailTenant, PvcName: "data"}
	started := time.Now()
	response, err := handler(context.Background(), request)
	if err != nil {
		t.Fatal("detail did not survive the 1s unary budget:", err)
	}
	if elapsed := time.Since(started); elapsed >= time.Second {
		t.Fatal("the blocked auxiliary group consumed the whole parent budget", elapsed)
	}
	detail := response.(*storagev1.GetVolumeResponse).Detail
	if detail.Volume.Uid != "pvc-uid-1" || len(detail.Consumers) != 1 {
		t.Fatal("main detail and healthy group were lost", detail.Volume, detail.Consumers)
	}
	if detail.EventsKnown || detail.EventsErrorCode != "VOLUME_EVENTS_UNAVAILABLE" || len(detail.Events) != 0 {
		t.Fatal("blocked events group must degrade with the fixed code", detail.EventsKnown, detail.EventsErrorCode)
	}
	if !detail.ConsumersKnown || detail.ConsumersErrorCode != "" {
		t.Fatal("healthy consumers group must stay known", detail.ConsumersKnown, detail.ConsumersErrorCode)
	}
}

// ST10: a parent context that is already cancelled or expired stops the detail
// before any auxiliary read and surfaces the standard gRPC context status.
func TestVolumeDetailParentCancellationReturnsGRPCContextStatus(t *testing.T) {
	svc := detailStubUseCase(t, &detailStubVolumes{record: detailRecord()})
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := svc.GetVolume(canceled, &storagev1.GetVolumeRequest{TenantId: detailTenant, PvcName: "data"}); status.Code(err) != codes.Canceled {
		t.Fatalf("canceled parent must map to Canceled, got %v", status.Code(err))
	}
	expired, cancelExpired := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancelExpired()
	if _, err := svc.GetVolume(expired, &storagev1.GetVolumeRequest{TenantId: detailTenant, PvcName: "data"}); status.Code(err) != codes.DeadlineExceeded {
		t.Fatalf("expired parent must map to DeadlineExceeded, got %v", status.Code(err))
	}
}

// ST06 (service mapping): the known flags and error codes ride on the wire
// response next to the main volume.
func TestVolumeDetailWireMappingCarriesGroupStatus(t *testing.T) {
	stub := &detailStubVolumes{record: detailRecord()}
	svc := detailStubUseCase(t, stub)
	response, err := svc.GetVolume(context.Background(), &storagev1.GetVolumeRequest{TenantId: detailTenant, PvcName: "data"})
	if err != nil {
		t.Fatal(err)
	}
	detail := response.Detail
	if !detail.EventsKnown || !detail.ConsumersKnown || detail.EventsErrorCode != "" || detail.ConsumersErrorCode != "" {
		t.Fatal("wire mapping lost the healthy group status", detail.EventsKnown, detail.ConsumersKnown, detail.EventsErrorCode, detail.ConsumersErrorCode)
	}
}
