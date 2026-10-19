# Instance configuration/network evidence index

This is a compact reproduction index, not another status ledger. Current results and remaining work live only in [execution status](../../../status.md#instance-configuration-and-network-2026-10-19). The [contract](../../../../specs/instance-configuration-network.md), [plan](../../../../plans/instance-configuration-network.md) and [runbook](../../../../runbooks/instance-mvp-fedora.md) own behavior and operations.

## Source and execution identity

- Goal: `01a15337-2e0a-7083-8b97-043204896c8d`; both review branches: `codex/instance-config-network-20261019`.
- Original design/plan SHA256: `deb4ad8170b83fbb8d0b5d533a0d10036331db99370ba735e93f91d1d99e8ae9` / `9f4347558f5869369f6aaafcb694a560fb47250b9464457ef64ff2b831d5027a`. The later explicit user decisions replace old-configuration compatibility and authorize shared test Gov/Res upgrades.
- Resource business commit: `5a224a98788fd4c9d6bbd2d50868ce9a330fa824`; officially generated artifact commit/formal consumer Origin: `3cd79a303d710617e513937ce5fc6f93ace8ccd5`.
- The user authorized immediate main integration without manual review. Resource PR #9 merged as `cea4eb8eb79f9fefec1ab2e55a50487119405974`; Gov PR #29 merged as `52922676ec4c047d6bc519bf84e3adc317af5159`. Their Git trees exactly equal the respective verified PR heads `3cd79a3` / `211a4d8`; merge metadata does not require rebuilding unchanged business code.
- Resource module: `v0.0.0-20261019123103-3cd79a303d71`; Sum `h1:BAivbcak6vhXEa2HNM5+OHgb0H5digyglAi14oQJmCk=`, GoModSum `h1:pvOlyTY++A80DtgFezkgXgo8eem2GYz2dRH4/Q42zYQ=`. Gov uses `GOWORK=off`, exact Origin and no replace.
- Resource source archive/manifest: `baec305dcf2bf8ee8196486b9fed49e40cdacd5d3d68ee01bf6fa3e42c1e36cf` / `4e2c39e6ac7b32bae77bbb09f3b508ca05f0a66fd090fb10553021eb2a3bcf69` (6,061 files). Later documentation and SBOM changes do not change this tested business source.
- Gov final normal merge `211a4d8a0415709b2cd484f1afc67f04e5ff3c9c`, tree `168a67f20ce88f38ffd435b10563c4b578a69fc9`, matches normalized source `gov-final-main53-s21-normalized`: archive `684491efbdb9bc9226e2e551524995c3d60028b849156c1ab031550f0e3340a2`, manifest `a67c2045df53c24f1af2423ddf0b56b9c304efc049f09f5641b059bb5f52e721` (2,316 files). Its committed generation check exits 0. Precommit binary VCS metadata remains `218c3759/modified=true`; every source byte matches the final committed tree, rather than claiming a direct committed build.
- Fedora run root `R=/home/chabking/workspace/ani-resource-service-runs/instance-config-network-20261019-01a15337`; private evidence `P=$R/private`, shared runtime `G=$P/shared-gpu`, installation `D=$P/shared-upgrade`. Local archive `L=/home/chabking/.codex/tasks/instance-config-network-20261019-01a15337`.
- Actual cwd and full file hashes are recorded per snapshot in `$R/input` and return manifests. Go 1.26.9, Buf 1.60.0, sqlc 1.31.1; existing `net05a-heavy.lock`, bounded scopes and private short TMPDIR. All heavy work, API drivers and cluster operations run through `ssh fedora`.
- Cluster API `https://172.16.101.10:6443`, context `kubernetes-admin@ani-lab`, kube-system UID `5277649d-e28d-4a0a-ae76-8354365c63bc`. Original shared tenant namespace UID `5b1539e9-e143-456e-8dc5-76fe9fff3c6a`; original ani-03 UID `af992ed9-cbe1-4f64-8fac-7433808bfab2` and T4 UUID `GPU-a86cb9fe-53ca-e474-5375-b662a54e8878` were checked before writes.

## Reproduction entry points

Use fresh task-owned resources and supported identities; the recorded lifecycle is already deleted. Do not replay old CREATE, cleanup intents, original DSNs or terminal tickets.

| Check | Actual entry point / retained receipt |
|---|---|
| Resource official generation, current configuration unit/real PG, required verify, static build, second-generation equality | `$L/private/res-current-contract-handoff.json`; `$P/verify-res-s21-green.sh`; all six recorded stages exit 0 |
| Committed Resource generation and official SBOM | `$P/res-s23-sbom.sh`; fixed CycloneDX v1.12.0; official generation, two stable SBOM generations and supply-chain verification exit 0 |
| Gov exact-commit generation | `$P/gov-committed-211a4d8a0415-generation.provenance.json`; complete source/tree identity and two full generations with zero differences; this receipt predates the separate final source-scan result |
| Gov final checks | `$P/gov-final-main53-s21-normalized.provenance.json`; 32 critical PG cases, race 3+1, generation/static/source/secret checks complete; original aggregate exit is not fabricated. Final head CI run `39274119334` has three successful jobs |
| Resource required PR CI | [PR #9](https://github.com/zhangzhe-ctrl/ani-resource-service/pull/9), run `39252688335`, seven successful jobs; optional recovery/LB mutation skips are not executed passes |
| CPU real Gov/PG/Notebook/CSI/SG lifecycle | `$P/cpu-live.py`, `$P/verify-retained.py`; original instance `5d54dd92-d96f-4b75-bcdb-87da5436e0c8` |
| CPU selected VPC data plane and historical release | `$P/vpc-live.py`, `$P/vpc-network-proof.py`, `$P/verify-vpc-retained.py`; original instance `d976e3a7-082c-4d9d-9d7a-890141897b09` |
| Shared GPU product lifecycle | `$P/shared-gpu-live.py`: configuration, negative, terminal rounds 0–3, traffic rounds, two Stop/Start rounds, normal Pod rebuild, maintenance replay, product DELETE and final wait; original instance `7919d13c-85c6-4467-98ff-403f81bf6444` |
| Actual CUDA | `$P/shared-gpu-iris-compute.sh` through formal Gov/SG; original and final Pod execution, not node-side math |
| Independent physical release / original-ledger refund | `$P/shared-gpu-release-proof.py`; actual read-only receipt below |
| Final Network release | `$P/shared-gpu-network-proof.py final-deleted deleted`; actual restricted runtime-role read-only snapshot 13, all historical original identities absent, eligible count 0 |
| User-volume retention after product DELETE | `$P/shared-gpu-retained.py start/status/cleanup-helper`; separate restricted read-only CSI inspector Succeeded, original three PVC/PV identities/files retained, dedicated workspace absent |

The frontend uses the [Gov container-instance HTTP contract](https://github.com/zhangzhe-ctrl/ani-governance/blob/52922676ec4c047d6bc519bf84e3adc317af5159/docs/contracts/container-instances.md): actual routes/JSON/pagination/permissions/errors and authorized configuration visibility. Browser behavior is independently `not_verified`.

## Selected immutable runtime receipts

Raw responses contain private tenant/configuration context and remain outside Git. These hashes identify the actual receipts rather than restating every response.

| Relative to private evidence roots | SHA256 |
|---|---|
| `G/evidence/gpu-cuda-round0.json` | `88fdb68eb83f28eb8a09f8beb419f6026c13b99800052aeebd062a4524d9ff25` |
| `G/evidence/gpu-cuda-final.json` | `1fae26ac820cf467709ae0815a1047993b52d1363c7ca3953b9df40c302731a9` |
| `G/evidence/gpu-final-physical-ledger-proof.json` | `4f068d48a531ad229aa7d58a8c9ddccfe08c4243af53bcf20640e439282aada9` |
| `G/evidence/vpc-network-final-deleted.json` | `e0d9cb5372826161b8648fdcbd28de0493f75cb756dae63e901d8972d1e4b8e9` |
| `G/evidence/retained-after-product-delete.json` | `0bb14b55a9f2a26518adf78eb5367fed5b43795a99d43c33de2022fce7ddc536` |
| `G/evidence/vpc-subnet-product-cleanup.json` | `98165c94da5fe9d404700eb304c158a3cb0f102c76733eb342f221291531f930` |
| `G/evidence/vpc-vpc-product-cleanup.json` | `a52a77662a5e75475d93019318101f1330ac7b79be81036dbb842667c30694bb` |
| `D/nic-list-installed-rbac/receipt.json` | `7a8e089ef3b19b685e6a9750f8482e6a1bed735714cb94ff30aebae9211065e3` |
| `G/fixture-cleanup/execution/receipt.json` | `92796c559cbe67e55657446e057cc1612797179a6798d5654ed1c2cc375290a3` |
| `P/gov-committed-211a4d8a0415-generation.provenance.json` | `6a5771487d5cbe9ff935193208a6a5f4fed9d3b91404dc3c2a9c6ef769d404ad` |
| `P/gov-final-main53-s21-normalized.provenance.json` | `83c6b6b5d178e8f264435d7fc04c02979588442575bb8d994ce03f1ec99c55e7` |
| `P/gov-211a-final-ci.json` | `4cdaf0d5449d3751760361a16afae71d68f70d67094a55860fe8b423f85afb37` |
| `P/evidence/ci-pg-final-cleanup.json` | `c7109bec6399bca6d3d1f395a87311805cafaaf6ca62ea55485452f832a3105f` |
| `D/final-environment-ready.json` | `3bb4b8a0dfed399ad011d892d8f76b60d3b37ec068b0fef281631b749f7e7814` |

## Failure and cleanup semantics

Original REDs, prerequisite errors, OOM 143 exits, transient SG/network waits and the 600-second final-release timeout are retained. In the last case a real Pod-bound Resource-SA token proved `kcn-system/nics list` 403. A namespace-only `list` Role bound solely to that SA changed the actual request to 200, and the original worker naturally completed. No manual completion/refund, repeated product DELETE, forced storage/network finalizer or direct PV deletion was used.

Product deletion retains borrowed volumes. Only after the independent retained-file proof are these explicitly disposable Goal fixtures normally deleted and their original PVs reclaimed by CSI. Peer, inspectors and network products have separate original UID checks. Existing shared namespace, unrelated resources, identities/trust, active installed images, Harbor release project/robot 17 and the narrow NIC read Role are retained installation dependencies. Exact final cleanup/deployment receipts are linked from the sole execution ledger.

The final test environment has both readers ready and both creation gates open; normal authenticated identity/catalog/configuration queries succeeded. The user stopped further vulnerability scanning and authorized immediate main integration. Main merge and final docs/SBOM-only commits use `[skip ci]` to avoid new scans; unchanged business gates are reused from exact PR heads 3cd/211a, while final committed generation and SBOM/Origin/notice metadata checks remain separate actual checks. No new full CI run is claimed for documentation-only publication.
