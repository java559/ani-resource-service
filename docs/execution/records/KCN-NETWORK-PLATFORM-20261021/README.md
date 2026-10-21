# KCN network platform run 20261021

Authoritative scope: [approved Goal](../../../plans/kcn-network-platform-goal-20261021.md).
Current progress is maintained only in [status](../../status.md).

[Res software validation](res-software-validation.json) records the fixed source
manifests, commands, original test-fixture failures, successful related slice,
and remaining verification boundaries. Runtime prerequisites are described in
[the deployment guide](../../../../deployments/egress/README.md).

[Formal API publication](formal-api-publication.json) fixes the published source,
canonical Go version, Origin and both checksum-database authenticated sums. It
also records the official module transfer, exact Gov dependency delta and
temporary transport cleanup; it does not establish joint business acceptance.

[Recovery boundary validation](recovery-isolated-validation.json) records the
Fedora r2 candidate's 17 passing tests, fixed hashes and observed cleanup. These
tests use loopback external boundaries; promotion, cluster use, maintenance and
actual restoration remain `NOT_RUN`. The current user confirmation and supported
KCN recovery protocol gates remain closed.

Approved inputs were read from `/home/chabking/workspace/ani-resource-service`
before being copied unchanged into this isolated worktree:

| Input | SHA256 |
|---|---|
| `docs/specs/kcn-v09-underlay-flow.md` | `48e017691ada3904e63bc483afa006da16d124743b307737cefd73003759bc01` |
| `docs/execution/records/kcn-network-flow-review-20261021.md` | `b646e3a2b6bf049670fd1fedd23ec6536ef76a61407a0bed4542de2ef89c66de` |

SSH fetch on 2026-10-21 fixed Resource main `6bff99f1747a495fe4b278844c20ee72b1ce2ffc`,
Governance main `323eb749b8a3d607b26687797b372919052895c7`, installer main
`78d8418307b6156954feb10aee8d6972d2ff1bbd`, and installer release
`8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166`. KCN GitLab SSH fetch exited 128
(`Connection closed by 192.168.20.144 port 22`); its `2b9467c` source remains an
explicit cache, and cannot be published as a verified latest candidate.

Resource branch: `codex/kcn-network-platform-20261021`; worktree:
`/home/chabking/.codex/worktrees/kcn-network-platform-20261021/ani-resource-service`.
All original dirty files and other worktrees are preserved. Runtime and heavy
commands execute only via Fedora, under task-owned directories and the existing
shared heavy-work lock. Private runtime snapshots stay on Fedora (0700/0600).

No public-egress maintenance is authorized to begin until the approved P4 guards,
restoration rehearsal, independent recovery channel and watchdog all pass.

The user's later explicit instruction prohibits all KCN source changes. It
supersedes the original Goal's narrow P1 implementation allowance. The three
task-modified KCN files were restored and three task-added tests removed from
the isolated KCN worktree; its HEAD remains cached `2b9467c` and status is clean.
No KCN commit, push, image, release or deployment occurred. Withdrawn candidate
tests are historical diagnosis only and cannot qualify the existing KCN runtime.
Res/Gov work continues within the revised boundary; no egress maintenance may
begin without a supported recovery protocol in the unchanged KCN deployment.

The user's subsequent instruction also requires confirmation before each
explicitly scoped phase of cluster use, including read-only API checks and Node
SSH. The original cluster preauthorization is superseded. Current independent
Fedora software/module work and isolated loopback tests may continue; no cluster
phase is currently approved.
