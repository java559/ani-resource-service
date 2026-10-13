package server

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	pb "github.com/zhangzhe-ctrl/ani-resource-service/api/instance/terminal/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
	"net"
	"os"
)

type InstanceTerminalServer struct {
	server  *grpc.Server
	address string
}

func NewInstanceTerminalServer(address, caFile, certFile, keyFile string, handler pb.InstanceTerminalServiceServer) (*InstanceTerminalServer, error) {
	if address == "" || handler == nil {
		return nil, fmt.Errorf("instance terminal address and validation required")
	}
	ca, e := os.ReadFile(caFile)
	if e != nil {
		return nil, fmt.Errorf("instance terminal CA unavailable")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(ca) {
		return nil, fmt.Errorf("instance terminal CA invalid")
	}
	cert, e := tls.LoadX509KeyPair(certFile, keyFile)
	if e != nil {
		return nil, fmt.Errorf("instance terminal server identity unavailable")
	}
	gs := grpc.NewServer(grpc.Creds(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{cert}, ClientCAs: roots, ClientAuth: tls.RequireAndVerifyClientCert})), grpc.UnaryInterceptor(func(ctx context.Context, r any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
		p, ok := peer.FromContext(ctx)
		if !ok {
			return nil, status.Error(codes.Unauthenticated, "SG_IDENTITY_REQUIRED")
		}
		t, ok := p.AuthInfo.(credentials.TLSInfo)
		if !ok || len(t.State.VerifiedChains) == 0 || len(t.State.PeerCertificates) == 0 || len(t.State.PeerCertificates[0].URIs) != 1 || t.State.PeerCertificates[0].URIs[0].String() != "spiffe://ani.internal/service/ani-session-gateway" || info.FullMethod != "/instance.terminal.v1.InstanceTerminalService/ValidateTerminalTarget" {
			return nil, status.Error(codes.PermissionDenied, "SG_IDENTITY_REQUIRED")
		}
		return next(ctx, r)
	}))
	pb.RegisterInstanceTerminalServiceServer(gs, handler)
	return &InstanceTerminalServer{gs, address}, nil
}
func (s *InstanceTerminalServer) Start(context.Context) error {
	listener, e := net.Listen("tcp", s.address)
	if e != nil {
		return e
	}
	e = s.server.Serve(listener)
	if e == grpc.ErrServerStopped {
		return nil
	}
	return e
}
func (s *InstanceTerminalServer) Stop(ctx context.Context) error {
	done := make(chan struct{})
	go func() { s.server.GracefulStop(); close(done) }()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		s.server.Stop()
		return ctx.Err()
	}
}
