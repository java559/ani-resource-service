package biz

import (
	"reflect"
	"slices"
)

// ResolveLoadBalancerRedirectTarget resolves against the complete saved
// configuration. Filtering first would incorrectly redirect to another port
// while the preferred HTTPS:443 listener waits for its first backend.
func ResolveLoadBalancerRedirectTarget(listeners []LoadBalancerListener) (LoadBalancerListener, bool) {
	var target LoadBalancerListener
	count := 0
	for _, l := range listeners {
		if l.Protocol != "HTTPS" {
			continue
		}
		if l.Port == 443 {
			return l, true
		}
		target, count = l, count+1
	}
	return target, count == 1
}

// ProjectLoadBalancerListeners is the single runtime projection. A configured
// backend remains part of the intent even when its weight is zero or its
// observed identity has become invalid; those conditions require safe runtime
// reconciliation rather than converting the listener into a waiting record.
func ProjectLoadBalancerListeners(listeners []LoadBalancerListener) []LoadBalancerListener {
	target, hasTarget := ResolveLoadBalancerRedirectTarget(listeners)
	var projected []LoadBalancerListener
	for _, l := range listeners {
		if l.Parameters.HTTPSRedirectStatusCode != 0 {
			if !hasTarget || len(target.Backends) == 0 {
				continue
			}
		} else if len(l.Backends) == 0 {
			continue
		}
		// Saved historical defaults and explicit client defaults produce the
		// same runtime specification. Canonicalize only this derived copy so a
		// database-only edit cannot require fresh Provider readiness merely
		// because SQL restored TCP or a client spelled out RoundRobin.
		if l.Protocol == "" {
			l.Protocol = "HTTP"
		}
		if l.Health.Protocol == "" {
			l.Health.Protocol = "TCP"
		}
		if l.Parameters.Algorithm == "" {
			l.Parameters.Algorithm = "RoundRobin"
		}
		l.DeploymentState, l.DeploymentReason = "", ""
		l.Backends = slices.Clone(l.Backends)
		if len(l.Backends) == 0 {
			l.Backends = nil
		}
		for j := range l.Backends {
			b := &l.Backends[j]
			b.AttachmentID, b.State, b.Reason, b.ObservedAt, b.ObservationStale = "", "", "", nil, false
		}
		slices.SortFunc(l.Backends, compareLBEndpoint)
		projected = append(projected, l)
	}
	slices.SortFunc(projected, func(a, b LoadBalancerListener) int {
		if a.Name < b.Name {
			return -1
		}
		if a.Name > b.Name {
			return 1
		}
		return 0
	})
	return projected
}

func LoadBalancerProjectionEqual(a, b []LoadBalancerListener) bool {
	return reflect.DeepEqual(ProjectLoadBalancerListeners(a), ProjectLoadBalancerListeners(b))
}

// SetLoadBalancerListenerDeploymentStates derives read-only state from saved
// intent, the current target, and persisted component facts. It never schedules
// work or claims that a configured listener has healthy application traffic.
func SetLoadBalancerListenerDeploymentStates(lb *LoadBalancer, components []LoadBalancerComponent) {
	listeners := slices.Clone(EffectiveLoadBalancerListeners(*lb))
	projected := ProjectLoadBalancerListeners(listeners)
	for i := range listeners {
		l := &listeners[i]
		active := slices.ContainsFunc(projected, func(p LoadBalancerListener) bool { return p.ID == l.ID && p.Name == l.Name })
		l.DeploymentState, l.DeploymentReason = "applying", ""
		if !active {
			l.DeploymentState, l.DeploymentReason = "waiting_for_backends", WaitingForBackends
			if l.Parameters.HTTPSRedirectStatusCode != 0 {
				l.DeploymentReason = WaitingForRedirectTarget
			}
			if lb.State == Deleting || (len(projected) == 0 && lb.ProviderTargetVersion > lb.AppliedVersion) {
				l.DeploymentState, l.DeploymentReason = "removing", CleanupPending
			}
			for _, c := range components {
				if c.ListenerID == l.ID && !c.Deleted && (c.CreateDispatched || c.Identity != "" || c.PendingAction != "") {
					l.DeploymentState, l.DeploymentReason = "removing", CleanupPending
				}
			}
		} else if lb.State == Deleting {
			l.DeploymentState, l.DeploymentReason = "removing", CleanupPending
		} else if lb.ConfigurationState == "unknown" || lb.Reason == ProviderUnknown {
			l.DeploymentState, l.DeploymentReason = "unknown", ProviderUnknown
		} else if lb.ConfigurationState == "degraded" || lb.State == Degraded {
			l.DeploymentState, l.DeploymentReason = "degraded", lb.Reason
		} else if lb.ConfigurationState == "configured" && lb.ProviderTargetVersion == lb.AppliedVersion {
			complete := true
			for _, kind := range LoadBalancerListenerComponentKinds(*l) {
				complete = complete && slices.ContainsFunc(components, func(c LoadBalancerComponent) bool {
					return c.ListenerID == l.ID && c.Kind == kind && !c.Deleted && c.Identity != "" && c.PendingAction == "" && c.AppliedVersion >= lb.ProviderTargetVersion
				})
			}
			if complete {
				l.DeploymentState = "configured"
			} else {
				l.DeploymentState, l.DeploymentReason = "unknown", ProviderUnknown
			}
		}
		for _, c := range components {
			if !c.Deleted && (c.ListenerID == l.ID || (c.Kind == "gateway" && len(projected) == 0)) && c.PendingAction == "create" && c.Identity == "" {
				l.DeploymentState, l.DeploymentReason = "unknown", ProviderUnknown
			}
		}
	}
	if len(lb.Listeners) != 0 {
		lb.Listeners = listeners
	} else {
		lb.Listener.DeploymentState, lb.Listener.DeploymentReason = listeners[0].DeploymentState, listeners[0].DeploymentReason
	}
}
