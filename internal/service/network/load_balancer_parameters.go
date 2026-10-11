package service

import (
	networkv1 "github.com/zhangzhe-ctrl/ani-resource-service/api/network/v1"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
)

func lbListenerProtocol(v networkv1.LoadBalancerListenerProtocol) (string, error) {
	switch v {
	case 0, 1:
		return "HTTP", nil
	case 2:
		return "HTTPS", nil
	case 3:
		return "TCP", nil
	case 4:
		return "UDP", nil
	case 5:
		return "SSL", nil
	default:
		return "", biz.Fail(biz.InvalidArgument, "invalid listener protocol")
	}
}

func lbProtocolToWire(v string) networkv1.LoadBalancerListenerProtocol {
	switch v {
	case "HTTPS":
		return 2
	case "TCP":
		return 3
	case "UDP":
		return 4
	case "SSL":
		return 5
	default:
		return 1
	}
}

func lbAlgorithmToWire(v string) networkv1.LoadBalancerAlgorithm {
	switch v {
	case "LeastRequest":
		return 2
	case "SourceIP":
		return 3
	default:
		return 1
	}
}

func lbListenerParameters(l *networkv1.LoadBalancerListenerInput) (biz.LoadBalancerListenerParameters, error) {
	p := biz.LoadBalancerListenerParameters{GzipEnabled: l.GzipEnabled, HTTPSRedirectStatusCode: l.HttpsRedirectStatusCode}
	switch l.Algorithm {
	case 0:
	case 1:
		p.Algorithm = "RoundRobin"
	case 2:
		p.Algorithm = "LeastRequest"
	case 3:
		p.Algorithm = "SourceIP"
	default:
		return p, biz.Fail(biz.InvalidArgument, "invalid listener algorithm")
	}
	if l.CertificateRef != nil {
		p.Certificate = &biz.LoadBalancerCertificateRef{Name: l.CertificateRef.Name, Namespace: l.CertificateRef.Namespace}
	}
	if l.SessionPersistence != nil {
		switch l.SessionPersistence.Mode {
		case 0, 3:
		case 1:
			p.SessionMode = "CookieTimeout"
		case 2:
			p.SessionMode = "CookieSession"
		default:
			return p, biz.Fail(biz.InvalidArgument, "invalid session mode")
		}
		p.CookieExpirySeconds = l.SessionPersistence.CookieExpirySeconds
	}
	if l.MaxConnections != nil {
		p.MaxConnections = *l.MaxConnections
	}
	if l.Protocol == networkv1.LoadBalancerListenerProtocol_LOAD_BALANCER_LISTENER_PROTOCOL_UDP && l.MaxConnections != nil {
		return p, biz.Fail(biz.InvalidArgument, "UDP does not accept connection parameters")
	}
	if l.Protocol != networkv1.LoadBalancerListenerProtocol_LOAD_BALANCER_LISTENER_PROTOCOL_UNSPECIFIED && l.Protocol != networkv1.LoadBalancerListenerProtocol_LOAD_BALANCER_LISTENER_PROTOCOL_HTTP && l.Protocol != networkv1.LoadBalancerListenerProtocol_LOAD_BALANCER_LISTENER_PROTOCOL_HTTPS && l.SessionPersistence != nil {
		return p, biz.Fail(biz.InvalidArgument, "Cookie parameters require HTTP or HTTPS")
	}
	return p, nil
}
