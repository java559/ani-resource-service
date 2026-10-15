package biz

// PageWindow is a direct database window, independent of any earlier request.
type PageWindow struct {
	Limit  int32
	Offset int64
}

// ResolvePage preserves explicit zero as invalid. uint32 bounds also keep the
// multiplication within int64, including the largest representable page.
func ResolvePage(page, pageSize *uint32) (PageWindow, error) {
	p, size := uint32(1), uint32(20)
	if page != nil {
		p = *page
	}
	if pageSize != nil {
		size = *pageSize
	}
	if p == 0 || size == 0 || size > 100 {
		return PageWindow{}, Fail(InvalidArgument, "page must be >= 1 and pageSize within 1..100")
	}
	return PageWindow{Limit: int32(size), Offset: (int64(p) - 1) * int64(size)}, nil
}
