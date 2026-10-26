package vm

import "context"

// ConsoleNativePort observes current runtime identities without making console
// access depend on every disk, backend volume or storage control plane read.
type ConsoleNativePort interface {
	ObserveConsole(context.Context, VM) (Observation, error)
}
