#!/usr/bin/env bash
set -euo pipefail
run=/home/chabking/ani-image-mvp-runs/update-97b13968-20261013
export PATH=/home/chabking/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.26.9.linux-amd64/bin:/home/chabking/ani-image-mvp-runs/img-merge-20261008/tools:$PATH
export GOWORK=off GOTOOLCHAIN=local GOMAXPROCS=2 GOFLAGS='-p=2 -trimpath' GOMEMLIMIT=1536MiB CGO_ENABLED=0
export GOMODCACHE=/home/chabking/ani-image-mvp-runs/img-20260929T1718Z/cache/go-mod
export GOCACHE=/home/chabking/ani-image-mvp-runs/img-20260929T1718Z/cache/go-build
cd "$run/source"
go version
go test -mod=readonly -count=1 ./internal/data/image ./internal/biz/image ./internal/service/image ./internal/server ./cmd/ani-resource-service
go test -mod=readonly -count=1 ./internal/biz/network -run 'TestLB(EmptyListeners|DeferredProjection|ProjectionPreserves|RedirectProjection|ListenerDeployment)'
go build -mod=readonly -trimpath -ldflags '-s -w -X main.Version=97b13968d156d65fe492cb583ec9920d21d0e57a' -o "$run/bin/resource" ./cmd/ani-resource-service
go mod verify
sha256sum "$run/bin/resource"
