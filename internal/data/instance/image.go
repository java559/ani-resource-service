package instance

import (
	"context"
	image "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/image"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/instance"
)

// Image is a narrow adapter to the existing public Image use case. Instance
// code neither reads Image tables nor reconstructs registry credentials.
type Image struct{ Runtime image.RuntimeImages }

func (i Image) Resolve(ctx context.Context, tenant, id, scope string) (biz.ResolvedImage, error) {
	if i.Runtime == nil {
		return biz.ResolvedImage{}, biz.ErrInvalid
	}
	ctx = image.WithCaller(ctx, image.Caller{Kind: image.RuntimeCaller, Subject: biz.Owner, TenantID: tenant})
	r, e := i.Runtime.ResolveImageForWorkload(ctx, image.ResolveImage{TenantID: tenant, ImageID: id, Scope: image.ImageScope(scope), TargetPlatform: image.ImagePlatform{OS: "linux", Architecture: "amd64"}})
	if e != nil {
		return biz.ResolvedImage{}, e
	}
	return biz.ResolvedImage{Reference: r.Reference, Digest: r.Digest}, nil
}
