package main

import (
	service "github.com/zhangzhe-ctrl/ani-resource-service/internal/service/instance"
	"testing"
)

func TestInstanceRequiresExplicitMatchingComputeTarget(t *testing.T) {
	const cluster = "11111111-1111-4111-8111-111111111111"
	cfg := instanceSettings{DatabaseDSNFile: "/managed/resource-runtime.dsn", Service: service.Config{ClusterID: cluster}}
	for _, c := range []*computeComponents{nil, {settings: computeSettings{DatabaseDSNFile: "/managed/other.dsn", ClusterID: cluster}}, {settings: computeSettings{DatabaseDSNFile: cfg.DatabaseDSNFile, ClusterID: "22222222-2222-4222-8222-222222222222"}}} {
		if requireInstanceCompute(c, cfg) == nil {
			t.Fatal("instance accepted a missing or different compute target")
		}
	}
	if e := requireInstanceCompute(&computeComponents{settings: computeSettings{DatabaseDSNFile: cfg.DatabaseDSNFile, ClusterID: cluster}}, cfg); e != nil {
		t.Fatal(e)
	}
}
