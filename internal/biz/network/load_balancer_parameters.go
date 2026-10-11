package biz

type LoadBalancerCertificateRef struct {
	Name, Namespace string
}

type LoadBalancerListenerParameters struct {
	Algorithm               string                      `json:",omitempty"`
	Certificate             *LoadBalancerCertificateRef `json:",omitempty"`
	SessionMode             string                      `json:",omitempty"`
	CookieExpirySeconds     uint32                      `json:",omitempty"`
	MaxConnections          uint32                      `json:",omitempty"`
	GzipEnabled             bool                        `json:",omitempty"`
	HTTPSRedirectStatusCode uint32                      `json:",omitempty"`
}

func validateLBListenerParameters(r LoadBalancerListenerInput) error {
	p := r.Parameters
	if p.Algorithm != "" && p.Algorithm != "RoundRobin" && p.Algorithm != "LeastRequest" && p.Algorithm != "SourceIP" {
		return Fail(InvalidArgument, "invalid listener algorithm")
	}
	tls := r.Protocol == "HTTPS" || r.Protocol == "SSL"
	if tls {
		if p.Certificate == nil || len(p.Certificate.Name) > 253 || !lbListenerName.MatchString(p.Certificate.Name) || (p.Certificate.Namespace != "" && (len(p.Certificate.Namespace) > 63 || !lbListenerName.MatchString(p.Certificate.Namespace))) {
			return Fail(InvalidArgument, "HTTPS and SSL require a same-namespace TLS Secret reference")
		}
	} else if p.Certificate != nil {
		return Fail(InvalidArgument, "certificate reference requires HTTPS or SSL")
	}
	http := r.Protocol == "HTTP" || r.Protocol == "HTTPS"
	if !http && (p.SessionMode != "" || p.CookieExpirySeconds != 0 || p.GzipEnabled || p.HTTPSRedirectStatusCode != 0) {
		return Fail(InvalidArgument, "Cookie, Gzip and redirect parameters require an HTTP listener")
	}
	if p.SessionMode != "" && p.SessionMode != "CookieTimeout" && p.SessionMode != "CookieSession" {
		return Fail(InvalidArgument, "invalid session persistence mode")
	}
	if (p.SessionMode == "CookieTimeout") != (p.CookieExpirySeconds > 0) {
		return Fail(InvalidArgument, "only CookieTimeout requires a positive cookie expiry")
	}
	if r.Protocol == "UDP" && (p.MaxConnections != 0 || r.Health.Protocol == "HTTP") {
		return Fail(InvalidArgument, "UDP accepts TCP health checks and no connection limit")
	}
	if p.HTTPSRedirectStatusCode != 0 && (r.Protocol != "HTTP" || (p.HTTPSRedirectStatusCode != 301 && p.HTTPSRedirectStatusCode != 302) || len(r.Backends) != 0) {
		return Fail(InvalidArgument, "HTTP redirect accepts 301 or 302 and excludes backends")
	}
	return nil
}

func LoadBalancerRouteKind(protocol string) string {
	switch protocol {
	case "TCP", "SSL":
		return "tcp_route"
	case "UDP":
		return "udp_route"
	default:
		return "route"
	}
}

func LoadBalancerIsRoute(kind string) bool {
	return kind == "route" || kind == "tcp_route" || kind == "udp_route"
}

func LoadBalancerListenerComponentKinds(l LoadBalancerListener) []string {
	kinds := []string{LoadBalancerRouteKind(l.Protocol), "policy"}
	if l.Parameters.MaxConnections > 0 {
		kinds = append(kinds, "client_policy")
	}
	if l.Protocol == "UDP" {
		kinds = append(kinds, "patch_policy")
	}
	return kinds
}
