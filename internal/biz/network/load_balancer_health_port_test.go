package biz

import "testing"

func TestLBExplicitHealthPort(t *testing.T) {
	for _, tc := range []struct {
		name    string
		port    uint32
		missing bool
		valid   bool
	}{
		{"missing-uses-backend-port", 0, true, true},
		{"zero-uses-backend-port", 0, false, true},
		{"out-of-range", 65536, false, false},
		{"independent-port", 9090, false, true},
		{"matches-listener-not-backend", 8080, false, true},
		{"matches-backend-not-listener", 80, false, true},
		{"minimum-port", 1, false, true},
		{"maximum-port", 65535, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			l, repo, ctx := lbTestSetup(t)
			r := lbTestRequest()
			r.Health.Port = &tc.port
			if tc.missing {
				r.Health.Port = nil
			}
			_, err := l.Create(ctx, r)
			if tc.valid {
				if err != nil || len(repo.accepted) != 1 {
					t.Fatalf("create: %v %+v", err, repo.accepted)
				}
				intent := repo.accepted[0]
				if intent.Health.Port != tc.port || intent.ListenerPort != 8080 || len(intent.Backends) != 1 || intent.Backends[0].Port != 80 {
					t.Fatalf("create changed health/listener/backend port intent: %+v", intent)
				}
			} else if ReasonOf(err) != InvalidArgument || len(repo.accepted) != 0 {
				t.Fatalf("invalid port accepted: %v", err)
			}
			_, err = l.Update(ctx, UpdateLoadBalancer{ID: "lb_11111111111111111111111111111111", ExpectedVersion: 1, IdempotencyKey: "update-port", LoadBalancerMutableInput: r.LoadBalancerMutableInput})
			if (tc.valid && err != nil) || (!tc.valid && ReasonOf(err) != InvalidArgument) {
				t.Fatalf("update: %v", err)
			}
			if tc.valid {
				if len(repo.accepted) != 2 {
					t.Fatalf("update did not reach persistence: %+v", repo.accepted)
				}
				intent := repo.accepted[1]
				if intent.Health.Port != tc.port || len(intent.Backends) != 1 || intent.Backends[0].Port != 80 || intent.Kind != "update_load_balancer" || intent.ExpectedVersion != 1 {
					t.Fatalf("update changed health/backend port intent: %+v", intent)
				}
			} else if len(repo.accepted) != 0 {
				t.Fatal("invalid health port reached persistence")
			}
		})
	}
}
