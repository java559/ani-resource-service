package biz

import (
	"slices"
	"testing"
)

func TestLBListenerSetContract(t *testing.T) {
	port := func(v uint32) *uint32 { return &v }
	request := lbTestRequest()
	request.LoadBalancerMutableInput = LoadBalancerMutableInput{Name: "two", Listeners: []LoadBalancerListenerInput{
		{Name: "first", Port: port(80), Backends: []LoadBalancerBackendInput{{SubnetID: request.SubnetID, Address: "10.2.1.2", Port: 8080}}, Health: LoadBalancerHealthInput{Port: port(8080)}},
		{Name: "second", Port: port(8081), Backends: []LoadBalancerBackendInput{{SubnetID: request.SubnetID, Address: "10.2.1.3", Port: 9000}}, Health: LoadBalancerHealthInput{Port: port(9000)}},
	}}
	l, repo, ctx := lbTestSetup(t)
	if _, err := l.Create(ctx, request); err != nil {
		t.Fatal(err)
	}
	first := repo.accepted[0]
	if len(first.Listeners) != 2 || first.Listeners[0].Health.Port != 8080 || first.Listeners[1].Health.Port != 9000 {
		t.Fatal("listener health/backend scope was flattened", first)
	}
	slices.Reverse(request.Listeners)
	if _, err := l.Create(ctx, request); err != nil {
		t.Fatal(err)
	}
	if first.Fingerprint() != repo.accepted[1].Fingerprint() {
		t.Fatal("listener order changed set fingerprint")
	}
	t.Run("tcp_protocol", func(t *testing.T) {
		r := request
		r.Listeners = slices.Clone(request.Listeners)
		r.Listeners[0].Protocol = "TCP"
		before := len(repo.accepted)
		if _, err := l.Create(ctx, r); err != nil {
			t.Fatal("TCP listener create rejected", err)
		}
		if _, err := l.Update(ctx, UpdateLoadBalancer{ID: "lb_11111111111111111111111111111111", ExpectedVersion: 1, IdempotencyKey: "update-tcp", LoadBalancerMutableInput: r.LoadBalancerMutableInput}); err != nil {
			t.Fatal("TCP listener update rejected", err)
		}
		if len(repo.accepted) != before+2 {
			t.Fatal("TCP listener create or update did not reach persistence")
		}
		for _, intent := range repo.accepted[before:] {
			index := slices.IndexFunc(intent.Listeners, func(v LoadBalancerListener) bool { return v.Name == r.Listeners[0].Name })
			if len(intent.Listeners) != 2 || index < 0 {
				t.Fatalf("TCP listener set was changed: %+v", intent)
			}
			listener := intent.Listeners[index]
			if listener.Protocol != "TCP" || listener.Port != *r.Listeners[0].Port || listener.Health.Port != *r.Listeners[0].Health.Port || len(listener.Backends) != 1 || listener.Backends[0].Address != r.Listeners[0].Backends[0].Address || listener.Backends[0].Port != r.Listeners[0].Backends[0].Port {
				t.Fatalf("TCP listener/backend/health intent was changed: %+v", listener)
			}
		}
	})
	for name, change := range map[string]func(*CreateLoadBalancer){
		"empty":                func(r *CreateLoadBalancer) { r.Listeners = []LoadBalancerListenerInput{} },
		"duplicate_name":       func(r *CreateLoadBalancer) { r.Listeners[1].Name = r.Listeners[0].Name },
		"duplicate_port":       func(r *CreateLoadBalancer) { r.Listeners[1].Port = r.Listeners[0].Port },
		"unsupported_protocol": func(r *CreateLoadBalancer) { r.Listeners[0].Protocol = "SCTP" },
		"mixed_legacy":         func(r *CreateLoadBalancer) { r.Backends = lbTestRequest().Backends },
		"too_many":             func(r *CreateLoadBalancer) { r.Listeners = make([]LoadBalancerListenerInput, 65) },
	} {
		t.Run(name, func(t *testing.T) {
			r := request
			r.Listeners = slices.Clone(request.Listeners)
			change(&r)
			before := len(repo.accepted)
			if _, err := l.Create(ctx, r); ReasonOf(err) != InvalidArgument {
				t.Fatal("invalid collection was admitted", err)
			}
			if len(repo.accepted) != before {
				t.Fatal("invalid collection reached persistence")
			}
		})
	}
}

func TestLBLegacyFingerprintContract(t *testing.T) {
	l, repo, ctx := lbTestSetup(t)
	if _, err := l.Create(ctx, lbTestRequest()); err != nil {
		t.Fatal(err)
	}
	i := repo.accepted[0]
	// Original pre-collection field order and types, frozen for historic replay.
	legacy := struct {
		TenantID, ID, Kind, IdempotencyKey                                           string
		ExpectedVersion                                                              int64
		Name, Description, VPCID, SubnetID, Exposure, Flavor, PublicEIPID, PrivateIP string
		ListenerPort                                                                 uint32
		Backends                                                                     []LoadBalancerBackend
		Health                                                                       LoadBalancerHealth
	}{i.TenantID, i.ID, i.Kind, "", i.ExpectedVersion, i.Name, i.Description, i.VPCID, i.SubnetID, i.Exposure, i.Flavor, i.PublicEIPID, i.PrivateIP, i.ListenerPort, i.Backends, i.Health}
	if i.Fingerprint() != egressFingerprint(legacy) {
		t.Fatal("legacy acceptance fingerprint changed")
	}
}
