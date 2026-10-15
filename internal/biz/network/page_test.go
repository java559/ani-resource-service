package biz

import (
	"math"
	"testing"
)

func TestResolvePage(t *testing.T) {
	ptr := func(n uint32) *uint32 { return &n }
	for _, tc := range []struct {
		name       string
		page, size *uint32
		limit      int32
		offset     int64
		bad        bool
	}{
		{"defaults", nil, nil, 20, 0, false},
		{"page only", ptr(3), nil, 20, 40, false},
		{"size only", nil, ptr(2), 2, 0, false},
		{"third", ptr(3), ptr(2), 2, 4, false},
		{"largest page", ptr(math.MaxUint32), ptr(100), 100, 429496729400, false},
		{"zero page", ptr(0), nil, 0, 0, true},
		{"zero size", nil, ptr(0), 0, 0, true},
		{"large size", nil, ptr(101), 0, 0, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w, err := ResolvePage(tc.page, tc.size)
			if tc.bad {
				if ReasonOf(err) != InvalidArgument {
					t.Fatalf("want invalid: %v", err)
				}
				return
			}
			if err != nil || w.Limit != tc.limit || w.Offset != tc.offset {
				t.Fatalf("window=%+v err=%v", w, err)
			}
		})
	}
}
