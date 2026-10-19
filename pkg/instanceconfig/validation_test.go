package instanceconfig

import (
	"errors"
	"testing"
)

func TestRejectDeployedAdmissionTriggers(t *testing.T) {
	for _, key := range []string{"serving.kserve.io/inferenceservice", "jobset.sigs.k8s.io/jobset-name", "leaderworkerset.sigs.k8s.io/name"} {
		if e := Validate("/work", nil, map[string]string{key: "injected"}, nil, nil, nil); !errors.Is(e, ErrReserved) {
			t.Fatal("deployed admission trigger accepted", key, e)
		}
	}
	if e := Validate("/work", nil, nil, nil, []string{"NB_PREFIX"}, []string{"private"}); !errors.Is(e, ErrReserved) {
		t.Fatal("Notebook runtime variable accepted", e)
	}
}
