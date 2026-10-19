package computemigrations

import "embed"

// Files contains forward migrations only. Recovery is an explicit owner action.
//
//go:embed *.sql recovery/*.sql
var Files embed.FS
