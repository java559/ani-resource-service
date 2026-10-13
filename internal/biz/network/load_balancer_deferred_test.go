package biz

import (
	"context"
	"slices"
	"testing"
	"time"
)

func TestLBEmptyListenersSaveValidatedIntent(t *testing.T) {
	for _, protocol := range []string{"HTTP", "HTTPS", "TCP", "UDP", "SSL"} {
		t.Run(protocol, func(t *testing.T) {
			l, repo, ctx := lbTestSetup(t)
			req := lbTestRequest()
			port := uint32(8080)
			listener := LoadBalancerListenerInput{Name: "waiting", Protocol: protocol, Port: &port}
			if protocol == "HTTPS" || protocol == "SSL" {
				listener.Parameters.Certificate = &LoadBalancerCertificateRef{Name: "tls-secret"}
			}
			req.LoadBalancerMutableInput = LoadBalancerMutableInput{Name: "waiting-lb", Listeners: []LoadBalancerListenerInput{listener}}
			if _, err := l.Create(context.Background(), req); ReasonOf(err) != PermissionDenied {
				t.Fatal("empty configuration bypassed authorization", err)
			}
			if _, err := l.Create(ctx, req); err != nil {
				t.Fatal("empty listener rejected", err)
			}
			if len(repo.accepted) != 1 || len(repo.accepted[0].Listeners) != 1 || len(repo.accepted[0].Listeners[0].Backends) != 0 {
				t.Fatal("saved intent did not preserve waiting listener", repo.accepted)
			}
			if len(LoadBalancerListenerComponentKinds(repo.accepted[0].Listeners[0])) != 0 {
				t.Fatal("waiting listener requested runtime components")
			}
			zero := uint32(0)
			req.Listeners[0].Health.TimeoutSeconds = &zero
			if _, err := l.Create(ctx, req); ReasonOf(err) != InvalidArgument {
				t.Fatal("empty listener bypassed health parameter validation", err)
			}
			if len(repo.accepted) != 1 {
				t.Fatal("invalid empty listener reached persistence")
			}
		})
	}
}

func TestLBDeferredProjectionKeepsZeroWeightAndInvalidIdentity(t *testing.T) {
	observed := time.Unix(1000, 0)
	configured := []LoadBalancerListener{
		{ID: "waiting", Name: "waiting", Protocol: "HTTP", Port: 80},
		{ID: "runtime", Name: "runtime", Protocol: "TCP", Port: 90, Backends: []LoadBalancerBackend{{ID: "member", SubnetID: "subnet", Address: "10.0.0.2", Port: 8080, Weight: 0, State: "degraded", Reason: BackendIdentityMismatch, ObservedAt: &observed}}},
	}
	projected := ProjectLoadBalancerListeners(configured)
	if len(projected) != 1 || projected[0].ID != "runtime" || len(projected[0].Backends) != 1 || projected[0].Backends[0].Weight != 0 {
		t.Fatal("runtime intent was mistaken for waiting configuration", projected)
	}
	configured[0].Port, configured[0].Health.Port = 8088, 1234
	configured[1].Backends[0].State, configured[1].DeploymentState = "available", "configured"
	if !LoadBalancerProjectionEqual(projected, configured) {
		t.Fatal("waiting edits or observation fields changed runtime projection")
	}
	configured[1].Backends[0].Weight = 1
	if LoadBalancerProjectionEqual(projected, configured) {
		t.Fatal("runtime backend weight change was ignored")
	}
	if projected[0].Backends[0].Weight != 0 {
		t.Fatal("projection aliases the saved configuration")
	}
}

func TestLBProjectionPreservesLegacyEquivalentRuntimeDefaults(t *testing.T) {
	legacy := LoadBalancerListener{ID: "listener", Name: "http", Port: 80, Backends: []LoadBalancerBackend{{ID: "member", SubnetID: "subnet", Address: "10.0.0.2", Port: 8080, Weight: 1}}, Health: LoadBalancerHealth{Port: 8080, IntervalSeconds: 5, TimeoutSeconds: 3, UnhealthyThreshold: 3, HealthyThreshold: 1}}
	stored := legacy
	stored.Protocol, stored.Health.Protocol, stored.Parameters.Algorithm = "HTTP", "TCP", "RoundRobin"
	intent := LoadBalancerIntent{Listeners: []LoadBalancerListener{legacy}}
	originalFingerprint := intent.Fingerprint()
	waiting := LoadBalancerListener{ID: "waiting", Name: "waiting", Protocol: "HTTP", Port: 8081}
	if !LoadBalancerProjectionEqual([]LoadBalancerListener{stored}, []LoadBalancerListener{legacy, waiting}) {
		t.Fatal("equivalent TCP/default HTTP/RoundRobin plus an empty listener changed the runtime projection")
	}
	projected := ProjectLoadBalancerListeners(intent.Listeners)
	if len(projected) != 1 || projected[0].Protocol != "HTTP" || projected[0].Health.Protocol != "TCP" || projected[0].Parameters.Algorithm != "RoundRobin" {
		t.Fatal("runtime defaults were not canonicalized", projected)
	}
	if intent.Fingerprint() != originalFingerprint || intent.Listeners[0].Protocol != "" || intent.Listeners[0].Health.Protocol != "" || intent.Listeners[0].Parameters.Algorithm != "" {
		t.Fatal("derived runtime defaults rewrote the accepted fingerprint/input")
	}
	for _, change := range []func(*LoadBalancerListener){
		func(l *LoadBalancerListener) { l.Parameters.Algorithm = "LeastRequest" },
		func(l *LoadBalancerListener) { l.Health.Protocol, l.Health.Path = "HTTP", "/ready" },
		func(l *LoadBalancerListener) { l.Protocol = "TCP" },
	} {
		changed := stored
		change(&changed)
		if LoadBalancerProjectionEqual([]LoadBalancerListener{stored}, []LoadBalancerListener{changed}) {
			t.Fatal("canonical defaults concealed a real runtime change", changed)
		}
	}
}

func TestLBRedirectProjectionResolves443BeforeFiltering(t *testing.T) {
	listeners := []LoadBalancerListener{
		{ID: "redirect", Name: "redirect", Protocol: "HTTP", Port: 80, Parameters: LoadBalancerListenerParameters{HTTPSRedirectStatusCode: 301}},
		{ID: "standard", Name: "standard", Protocol: "HTTPS", Port: 443},
		{ID: "alternate", Name: "alternate", Protocol: "HTTPS", Port: 8443, Backends: []LoadBalancerBackend{{ID: "alternate-member", Weight: 1}}},
	}
	target, ok := ResolveLoadBalancerRedirectTarget(listeners)
	if !ok || target.ID != "standard" {
		t.Fatal("complete configuration lost preferred HTTPS target", target, ok)
	}
	if p := ProjectLoadBalancerListeners(listeners); len(p) != 1 || p[0].ID != "alternate" {
		t.Fatal("redirect silently changed its target after filtering", p)
	}
	listeners[1].Backends = []LoadBalancerBackend{{ID: "standard-member", Weight: 0}}
	if p := ProjectLoadBalancerListeners(listeners); len(p) != 3 {
		t.Fatal("activating preferred target did not activate redirect", p)
	}
	slices.Reverse(listeners)
	if p := ProjectLoadBalancerListeners(listeners); len(p) != 3 || p[0].Name != "alternate" || p[1].Name != "redirect" || p[2].Name != "standard" {
		t.Fatal("projection order is not deterministic", p)
	}
	listeners[1].Backends = nil
	if p := ProjectLoadBalancerListeners(listeners); len(p) != 1 || p[0].ID != "alternate" {
		t.Fatal("clearing preferred target did not retire redirect", p)
	}
}

func TestLBListenerDeploymentUsesCleanupAndComponentFacts(t *testing.T) {
	lb := LoadBalancer{DesiredVersion: 3, ProviderTargetVersion: 2, AppliedVersion: 2, ConfigurationState: "configured", Listeners: []LoadBalancerListener{
		{ID: "runtime", Name: "runtime", Protocol: "HTTP", Backends: []LoadBalancerBackend{{ID: "member"}}},
		{ID: "waiting", Name: "waiting", Protocol: "TCP"},
	}}
	components := []LoadBalancerComponent{{ID: "route", Kind: "route", ListenerID: "runtime", Identity: "uid-route", AppliedVersion: 2}, {ID: "policy", Kind: "policy", ListenerID: "runtime", Identity: "uid-policy", AppliedVersion: 2}}
	SetLoadBalancerListenerDeploymentStates(&lb, components)
	if lb.Listeners[0].DeploymentState != "configured" || lb.Listeners[1].DeploymentState != "waiting_for_backends" {
		t.Fatal("pure DB edit invalidated runtime or waiting listener", lb.Listeners)
	}
	lb.Listeners[0].Backends = nil
	lb.ProviderTargetVersion, lb.ConfigurationState = 4, "applying"
	SetLoadBalancerListenerDeploymentStates(&lb, components)
	if lb.Listeners[0].DeploymentState != "removing" {
		t.Fatal("clearing backends reported waiting before runtime release", lb.Listeners)
	}
	components[0].PendingAction, components[0].Identity = "create", ""
	SetLoadBalancerListenerDeploymentStates(&lb, components)
	if lb.Listeners[0].DeploymentState != "unknown" || lb.Listeners[0].DeploymentReason != ProviderUnknown {
		t.Fatal("empty intent erased unknown CREATE", lb.Listeners)
	}
	components[0].Deleted, components[1].Deleted = true, true
	lb.AppliedVersion, lb.ConfigurationState = 4, "waiting_for_backends"
	SetLoadBalancerListenerDeploymentStates(&lb, components)
	if lb.Listeners[0].DeploymentState != "waiting_for_backends" {
		t.Fatal("confirmed cleanup did not return listener to waiting", lb.Listeners)
	}
}
