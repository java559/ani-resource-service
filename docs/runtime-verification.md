# Resource 运行验证

重任务按 [远程约定](remote-execution.md) 优先在 ubuntu 的独立快照执行。可复现入口：

```bash
make tools
make verify
make integration
make race
make tenant-mutations
make supply-chain-tools
make audit
```

`make verify` 核对固定 Buf 1.60.0 / sqlc 1.31.1，执行 Proto/config/sqlc 再生成前后完整文件清单对比，检查分层依赖、格式、module tidy、单元与通用运行测试、vet、build、module checksum 和 Git 空白。Proto Go / gRPC 插件分别固定为 1.36.11 / 1.5.1，不手写生成物。

`make integration` 使用固定 PostgreSQL 18.6 amd64 镜像，启动 loopback 随机端口的独占容器。每个测试创建独立数据库、owner/runtime 角色，迁移和 fixture 通过 owner 执行，业务通过 runtime 角色运行。容器和角色均由本次测试清理；凭据随机生成且不打印。镜像 digest、容器 ID 和清理结果进入命令输出。无需宿主 psql、sudo 或 kind。

没有 `NETWORK_TEST_ADMIN_DSN` 时，普通 `go test` 会明确 skip 真实数据库/生产组合根/独立进程测试；它不能代替带真实数据库的入口。CI 用 `make race` 执行这些用例及竞态检查，再执行租户谓词变异门禁；`make integration` 保留为开发时不带 race 的入口，不在 CI 重复执行同一套集成测试。不要使用 `-short` 或删除检查绕过它们。

`make race` 在同一真实 PostgreSQL 设施下执行全包 race 测试。恢复测试还构建带 race detector 的实际服务二进制，保留同一数据库与受控 Provider 事实，在 T1 之后、Provider 成功但 T4 之前、删除结果未知时终止进程并恢复；不会重建空库冒充重启。测试还覆盖不同服务进程共享持久租约，以及墓碑处理保留历史 operation。

候选进程使用独占测试 CA、服务端证书和 Governance 客户端证书，调用经生产 mTLS 与可信 metadata 校验。历史客户端固定为 `66f787b` 的生成契约，仅通过临时编译 overlay 适配测试驱动的 TLS/context；原件与 protobuf 摘要保持不变。同库回滚使用 schema 兼容的固定基线 `b258907`，不把缺少后续迁移的旧服务作为当前数据库的可运行回滚目标；迁移版本、checksum、应用时间、回执、cursor 和 Provider 身份仍须保持。

`make tenant-mutations` 分别破坏 GetVPC、GetOperation、ListVPCs、GetSubnet、ListSubnets 和 GetAttachment 的 tenant 过滤，再用 sqlc 生成并执行真实数据库行为测试。只有相应跨租户断言实际失败才算识别成功；编译失败或其他测试错误不能充数。脚本始终恢复输入 SQL 和生成物，变异不留在最终代码。

Image 的真实数据库用例使用 `imageintegration` build tag，普通 `go test ./...` 和 Network 数据库入口均不包含它们。单独执行：

```bash
export TMPDIR=$(mktemp -d /tmp/ani-image-checks.XXXXXX)
trap 'rmdir "$TMPDIR"' EXIT
scripts/image-integration -race
make image-tenant-mutations
```

`scripts/image-integration` 创建独占 PostgreSQL、owner/runtime 角色和私有 DSN 文件，覆盖 Image 持久化、生命周期、并发与凭据合同；`-race` 同时执行竞态检查。`make image-integration` 保留为不带 race 的开发入口。Image 租户变异门禁使用同一数据库准备入口，测试结束恢复变异并清理自有资源。

CI 对 PR 和 `main` push 执行独立的源码/单测、Network 数据库与隔离、Image 数据库与隔离、供应链 jobs。最终 `verify` 汇总要求每一项成功，失败、取消或跳过均不能通过。连续更新同一 PR 会取消过时执行，各 job 有明确时间预算；任务分支 push 不再重复触发整套流水线。

主要测试入口：

| 位置 | 证据范围 |
|---|---|
| [data 集成测试](../internal/data/network/postgres_integration_test.go) | 原子受理/回滚、租户边界、分页、角色隔离和无 RLS |
| [worker 故障测试](../internal/data/network/faults_integration_test.go) | 同键并发、迟到 HTTP POST、租约和 epoch、纯查询/观测过期 |
| [kc 契约测试](../internal/data/network/kc_integration_test.go) 与 [拒绝路径](../internal/data/network/provider_faults_integration_test.go) | 真实 dynamic client、VPC spec、UID/resourceVersion 删除条件、归属/占用/失联/拒绝 |
| [RPC 测试](../internal/data/network/grpc_integration_test.go) | 生成契约、真实数据库、错误映射和租户隔离 |
| [独立进程测试](../internal/data/network/process_integration_test.go) | 实际二进制和原有持久事实的故障恢复 |
| [生产装配](../cmd/ani-resource-service/app_test.go) / [进程信号](../cmd/ani-resource-service/main_test.go) / [worker 生命周期](../internal/server/worker_test.go) | readiness、健康、异常退出与优雅停机 |
| [通用运行回归](../tests/runtime/runtime_test.go) | 日志、trace、metrics、中间件、生成配置和 reflection |

供应链流程保留原门禁：固定 govulncheck 1.7.0、Gitleaks 8.30.1、cyclonedx-gomod 1.12.0；需要 jq 和 rg。`make audit` 扫描依赖、当前 Git 全 refs，并检查 notice 与许可证据、生成 Linux/amd64 runtime SBOM。SBOM 要求源 checkout 已提交且干净（仅允许 SBOM 输出变化）；本轮使用经核对源码快照的远程临时验证提交，不提交原仓库。原仓库历史扫描与远程单快照扫描须分别记范围。CI 使用 full-history checkout。

SBOM 生成使用除旧 SBOM 外的跟踪源码建立固定作者/时间的 synthetic source commit，删除扫描器自身构建 hash，输出不含随机 serial/timestamp。重新构建的扫描器在固定 module/version 门禁下可复现输出；扫描时点和工具二进制 hash 留在执行日志。源文档变化也会改变 synthetic source identity，因此最终文档完成后须同步生成 SBOM。

最新 pass/fail/not_verified 只在 [执行状态](execution/status.md) 维护，NET-01 证据见 [实施记录](execution/records/NET-01-implementation.md)。真实 Kubernetes/kc/OVN 数据面、Console、VM、IAM 和生产发布各自保持独立验收边界。

## NET-02–04 双仓库接口验证

`scripts/remote-pair -- <argv>` 从固定 ANI 专用 worktree 与 Network 工作树建立各自源清单和归档，在 ubuntu 新建目录；不包含 ANI 主 checkout 改动或兄弟运行模块。`scripts/integration-pair` 创建专属 PG 容器，ANI 与 Network 使用不同数据库/owner/runtime。环境变量 `NET0204_TEST_ADMIN_DSN` 只由该入口设置，专项测试没有 live DSN fallback。

```bash
scripts/remote-pair -- bash -c '"$NETWORK_SOURCE/scripts/integration-pair" "$NETWORK_SOURCE/scripts/test-net0204-wire"'
```

该入口构建独立 Network 服务、实际 Gateway 路由测试进程与 ANI 实例 owner 测试进程。HTTP fixture 只提供固定 kc/Kubernetes 契约和可控故障事实；使用真实 client、renderer、持久实例受理/封闭、gRPC consumer 查询与数据库恢复，不构成真实 kc 控制器、OVN 或 IAM 认证证明。具体故障场景、执行结果与源码身份见 [组合记录](execution/records/NET-02-04-implementation.md)。

## NET-05A 持续观察与真实复验

单仓重任务入口为 `scripts/net05a-remote -- scripts/net05a-gates`，从专用 dirty worktree 建立逐文件校验快照。`scripts/net05a-stage` 提供开发期间定向生成与真实 PG 测试；不能替代完整门禁。观察受控用例位于 [kc_observation_integration_test.go](../internal/data/network/kc_observation_integration_test.go)，通知/公平性位于 [observation_integration_test.go](../internal/data/network/observation_integration_test.go)。协议 fixture 使用真实 dynamic client 的 HTTP List/Watch，仅证明受控层。

固定容量入口为 `scripts/net05a-remote -- scripts/net05a-capacity --matrix --verify-first`。同一进程串行执行旧/新版本、100/1000/2000 Attachment、1/2 副本和五个固定阶段；每组独立真实 PG。条件与随机种子见 [容量合同](execution/records/NET-05A/capacity-contract.json)。导出后通过 `scripts/net05a-capacity-report <run-directory>` 生成判定，保留基线失败、丢失/截尾样本和未完成组；Go 测量程序 exit=0 不等于性能通过。

本轮用户允许资源限制下的大负载延期后，补验使用 `scripts/net05a-remote -- scripts/net05a-capacity --small-matrix --variants net05a`，只重测固定的 100 Attachment / 1、2 副本。报告的 `--baseline-from <prior-run-directory>` 仅引用旧版已完成组，保留各组源目录和摘要；不能据此将全矩阵写为通过。

真实链路使用 `scripts/net05a-pair -- python3 -B network/scripts/net05a-live.py build` 建立精确双仓隔离构建。后续对同一 `NET05A_RUN_DIR` 依次运行 `positive`、`setup-faults`、`queries`、`transactions`、`lease`、`unknown`、`provider`、`owner`、`watch`、`rollback`、`product-cleanup`、`gates`、`export`、`fixture-cleanup`。每阶段先检查已保存身份与结果，不盲目重放失败写入；新源码快照沿用旧 run 时仍须保留实际构建/运行身份映射。NET-05A wrapper 核对并适配固定 NET-05 runner，原脚本保留为历史输入。

本包重要修复后的精确新构建可用 `upgrade` 保留原失败意图并验证恢复，再用 `NET05A_WAVE=<unique-wave>` 的 `recheck-products` 经产品 API 清理并创建新普通容器波次。两阶段有已启动检查点，失败后先检查记录；不会自动重放。`NET05A_WAVE` 同时隔离后续故障用例的永久幂等键，测试暂停点必须使用同一源码构建。

新 V-09 分别证明全部后台暂停时查询零副作用，以及只暂停状态应用、保留 Watch 时持久证据仍过期并拒绝准入。正常 Network 使用新镜像；明确标记的测试构建只暂停本包进程。真实 Watch、真实 PG、普通容器数据面、受控 403/429/410 和容量证据各自分列。ANI 固定源码不修改；既有八条 compatibility 与全历史 Atlas 失败不能由 schema fixture 改写。详细源身份、实际命令和结果见 [NET-05A 记录](execution/records/NET-05A-implementation.md)。
