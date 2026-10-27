package server

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"strings"
	"testing"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
)

func TestWorkerDiagnosticsLogProviderCauseAndResourceContext(t *testing.T) {
	o, err := NewObservability("test", "test", NewReadiness(func() bool { return true }))
	if err != nil {
		t.Fatal(err)
	}
	defer o.Shutdown(context.Background())
	var output bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&output, nil))
	work := biz.Work{Resource: biz.ResourceWork{Kind: "vlan", ID: "vlan-id"}, BindingID: "binding-id", Operation: biz.Operation{ID: "operation-id"}}
	o.ObserveWork(context.Background(), logger, work, biz.Progress{Reason: biz.ProviderUnavailable, ProviderError: &biz.ProviderError{Kind: biz.ProviderTemporary, Cause: errors.New("client rate limiter wait: context deadline exceeded")}}, nil)
	for _, want := range []string{"client rate limiter wait: context deadline exceeded", "vlan-id", "binding-id", "operation-id"} {
		if !strings.Contains(output.String(), want) {
			t.Fatalf("missing diagnostic %q: %s", want, output.String())
		}
	}
}
