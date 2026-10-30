package storage

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func TestPresignPutRequiresConditionalHeader(t *testing.T) {
	client := s3.New(s3.Options{Region: "us-east-1", BaseEndpoint: aws.String("https://storage.example"), UsePathStyle: true, Credentials: credentials.NewStaticCredentialsProvider("test-access", "test-secret", "")})
	adapter := &S3{internal: client, presign: s3.NewPresignClient(client), browser: true}
	for _, overwrite := range []bool{false, true} {
		raw, headers, expires, err := adapter.PresignPutObject(context.Background(), "bucket-test", "docs/中文 +%.txt", "text/plain; charset=utf-8", overwrite, 15*time.Minute)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := url.Parse(raw)
		if err != nil {
			t.Fatal(err)
		}
		if parsed.Host != "storage.example" || parsed.Path != "/bucket-test/docs/中文 +%.txt" || parsed.Query().Get("X-Amz-Expires") != "900" || !expires.After(time.Now()) {
			t.Fatalf("incorrect signed target")
		}
		if _, found := headers["Host"]; found {
			t.Fatal("browser must not set Host")
		}
		signed := parsed.Query().Get("X-Amz-SignedHeaders")
		if !overwrite && (!strings.Contains(signed, "if-none-match") || headers["If-None-Match"] != "*") {
			t.Fatalf("conditional header missing: signed=%s headers=%v", signed, headers)
		}
		if overwrite && strings.Contains(signed, "if-none-match") {
			t.Fatal("overwrite unexpectedly conditional")
		}
		if headers["Content-Type"] != "text/plain; charset=utf-8" {
			t.Fatal("content type changed")
		}
	}
}

func TestS3DeleteForwardsCondition(t *testing.T) {
	called := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		if r.Method != "DELETE" || r.URL.Path != "/bucket-test/docs/a.txt" || r.Header.Get("If-Match") != `"abc"` {
			t.Errorf("unexpected delete request: %s %s condition=%s", r.Method, r.URL.Path, r.Header.Get("If-Match"))
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	client := s3.New(s3.Options{Region: "us-east-1", BaseEndpoint: aws.String(server.URL), UsePathStyle: true, Credentials: credentials.NewStaticCredentialsProvider("test-access", "test-secret", "")})
	if err := (&S3{internal: client}).DeleteObject(context.Background(), "bucket-test", "docs/a.txt", `"abc"`); err != nil {
		t.Fatal(err)
	}
	if !called {
		t.Fatal("delete never reached HTTP boundary")
	}
}
