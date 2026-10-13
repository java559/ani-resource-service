# 2026-10-13 后端定向验证记录

这是本次源码时点证据；唯一当前状态在 [status.md](../../status.md#gpu-container-instance-mvp-v15)。规格与运行方法分别见 [v1.5](../../../specs/instance-mvp.md) 和 [Fedora 手册](../../../runbooks/instance-mvp-fedora.md)。四仓基线、任务 worktree 和环境 UID 由当前状态记录，不重复建立状态账本。

后续用户授权等待 GPU 期间推进的实际 scheduler 适配、CPU Notebook/SG/CSI 及隔离生产后端准备见 [真实实验索引](live-lab.md)，包含准确来源、日志、资源 receipt 和恢复入口。

其后实际 Image 登记、租户拉取材料及入口验证见 [Image 实验](image-lab.md)：真实 OCI 报文和 `img_…` 标识接线已修复；候选要求 Jupyter token，入口验证 fail，未提升为 GPU 兼容。

## 固定执行环境

所有依赖、生成、格式化、编译、测试、PG 和 API 驱动均为 `ssh fedora`。任务根 `/home/chabking/workspace/ani-resource-service-runs/instance-mvp-v15-20261013`；重任务复用 `/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock`，`systemd-run --user --scope -p CPUQuota=200% -p MemoryMax=6G -p MemorySwapMax=0`。Go 1.26.9，`GOMAXPROCS=2`、`GOFLAGS=-p=2`、`GOMEMLIMIT=5GiB`，独占 mod/go/tmp 缓存；Buf 1.60.0、sqlc 1.31.1。

PG 使用固定 `docker.io/library/postgres@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280`，任务标签、768 MiB/1 CPU、随机 loopback 端口、tmpfs。测试准备使用 owner，服务使用非超级用户、非 owner 受限 runtime；fixture 输出不记录 DSN/密钥。Res 子进程的 PG 和服务 mTLS 凭据均任务独占，退出通过 stdin stop，role/database 自动清理。没有产品完成状态 SQL 注入；故障用例只调整独占工作队列的 due/lease 时钟。

## 实际命令与结果

| 命令（任务工作区） | 退出码 | 证明范围 |
| --- | --- | --- |
| Resource `scripts/generate-instance-api`；sqlc generate | 0 | 具名实例/窄终端 API 与本域 SQL 生成 |
| Gov `scripts/generate-instance-slice.sh`；Ent v0.14.6 生成及 schema 导出 | 0 | 新 INSTANCE DTO/HTTP/OpenAPI/枚举及 Ent 注册 |
| Acc Buf conf、SG Buf session 生成 | 0 | 新 owner 公钥配置与冻结目标字段 |
| 四仓 `go build ./cmd/ani-resource-service`、`./app/admin/service/cmd/server`、`./cmd/ani-accelerator-service`、`./cmd/session-gateway` | 0 | 目标服务可编译，使用各自隔离任务 workspace |
| 窄终端模块 `GOWORK=off go mod tidy`、`GOWORK=off go build ./v1` | 0 | protobuf/gRPC 单独构建，不证明发布版本已可下载 |
| Resource `go test -count=1 ./internal/data/instance -run '^TestNotebookMappingAndConditionalStop$'` | 0 | 冻结 recipe/Queue、原生停止注解/条件版本、真实 Pod Ready 与 UID/workspace 投影；动态 Kubernetes 边界受控 |
| Resource `scripts/instance-integration` | 0 | 4 个具名真实 PG 用例：生命周期/未知 CREATE、删除先到/租户、生产退额 outbox/终端撤销、迟到 receipt/lease fence |
| Gov `go test -tags=quota_pg -count=1 ./app/admin/service/internal/service -run '^TestInstanceCurrentAuthorizationOriginalReplayAndOwnerRelease$'` | 0 | 当前授权、原公开请求先重放、异内容/撤权拒绝、真实 Resource mTLS ACK、合法原账声明/累计幂等与 ENDED 派生 |
| Gov `go test -tags=quota_pg -count=1 ./app/admin/service/internal/server -run '^TestInstancePublicHTTPPersistentIntakeAndCommands$'` | 0 | 正式 HTTP/JWT/套餐/Casbin/当前授权、真实 Gov/Res PG、CREATE/DELETE 持久 ACK、启停原账不变；Notebook 未运行，原账仍占额 |
| Gov `go test -tags=quota_pg -count=1 ./app/admin/service/internal/data -run '^TestQuotaGpuDeleteReleaseAndProjectionRecovery$'` | 0 | 受影响旧 Inference 原账/释放/同步恢复路径 |
| Gov constants `-run '^TestServiceTagToBusinessModule'` 及旧 frozen canonical/projection/owner 注册具名测试 | 0 | INSTANCE 分类及必要旧合同 |
| Acc `go test -count=1 ./internal/biz ./internal/data/provider -run '^(TestContractVectors\|TestSyncProjectionFactsAndTerminalShape\|TestAttestationAndOwnerTrustBoundaries)$'` | 0 | Resource ref/ENDED、公钥隔离、必要旧规范向量 |
| SG session/PodResolver/Redis/gRPC 具名测试；新增 ExecGuard/RedisBinding/GeneratedClient 三组 | 0 | 冻结 UID、票据重启恢复、替换 Pod 拒绝及必要旧调用；exec UID guard 使用受控外部 executor |

Resource 最后日志为 `evidence/resource-final.log`，Gov HTTP/构建为 `evidence/gov-api.log`；任务私有 runner、输入文件哈希和源归档仍保留在任务根。r9 Resource 完整归档 `1dbd55accdfd95039b14eec8bad1d1ca9a96d18295df4854a0f490021bfa466b`，后续 r10 `5853cd42ee46190a602e381197891ad51aeb18c71fdf734dcf93c581501f77c0`，再由具名 delta 文件哈希冻结上述新增修复。生成/格式化按文件白名单和本地写入保护回传，源输入和返还归档校验保持。

## 失败与修复边界

- 共享缓存权限、上游暂存只读清理、构造字段及 workspace `-mod=mod` 错误已限定修复；没有修改全局缓存或换主机。
- 混合根模块曾选择 SG 不兼容的 Kubernetes/OpenAPI 图；改为窄终端 API 与独立 consumer workspace，目标构建通过。
- 初次 Acc 测试正则没有匹配用例，明确不记 pass；修正为上表实际具名测试并执行通过。
- 退额事件重放的累计总量保持、applied_delta=0 是正确结果；初次测试误要求重复 applied_delta 已修正。
- 新公开链路发现 INSTANCE 套餐转换/tag 缺失、OpenAPI 路径 json_name 不一致，以及删除 expected_version 未持久化；已修复并由完整具名 HTTP 切片验证。非法内部身份 header 入口拒绝属于合同预期，不作为业务成功。
- Res 同值 Stop 写入失败曾能借旧 observed 状态显示完成；完成条件增加无失败/无待定动作，并由真实 PG 定向恢复用例验证。

本轮没有运行 `go test ./...`、全量 race/mutation、压测或扫描，没有推送/PR/合并。上述受控事实和替身行为不能证明真实共享/整卡 GPU、物理盘回收、真实 SG 终端文件保留或 Acc 设备已空闲。

## 同日原账归还与 Acc 同步切片

`INSTANCE_JOINT_LIFECYCLE=1` 执行同一 `TestInstancePublicHTTPPersistentIntakeAndCommands`，44.98 秒、exit 0。Acc test-only 进程使用正式服务、真实受限 PG 和服务 mTLS；供给/验证 provider 是明确外部硬件替身，目录由原管理 RPC 创建，不直接写目录或使用投影表。Gov 公共链路随后通过真实 Acc Catalog RPC 冻结单副本 workspace plan，真实 Res worker 的清理决策触发生产 outbox，经 Resource DNS owner mTLS 调用 Gov 原账，余额归零后由真实同步 worker 写入 Acc，最后通过其服务读取 ENDED。Kubernetes provider、Image/环境事实仍受控，尚未证明真实 GPU、Notebook、专属盘或终端。

发现并修复 Acc 数据库的单 owner 约束：新增 0003，不改 0001/0002；独占 fixture 实际执行三步迁移。另执行 `go test -count=1 ./internal/data -run '^(TestPostgresProjectionOrderConflictAndRestart|TestPostgresBindingsBeforeProjectionAndScopedRelease)$'` exit 0，包含原 Inference 与 Resource 两个固定 owner；Binding 先到仍计容量，DECLARED 关联后 ENDED 不自动解除，只有确定外部释放事实解除。此检查只证明受控持久化与原消费者边界。

日志分别为 [lifecycle.txt](lifecycle.txt)、[acc-owner.txt](acc-owner.txt)，对应远端 `evidence/lifecycle.log` 与 `evidence/acc-owner.log`；runner 和本次输入哈希保留在任务 `private/`。初次 Resource fixture 编译发生 import 名称与既有测试 type 重名，限定改别名后重跑相关编译/切片通过。任务容器/卷已按标签清理；测试 PKI、目录和子进程由各 fixture 回收。当前结果只更新 status.md。

## 同日 SG 会话切片

继续复用同一 HTTP 用例，加入真实 SG 进程及独占 Redis，45.00 秒、exit 0；[日志](terminal.txt) 对应远端 `evidence/terminal.log`。使用固定 Redis digest `sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`，256 MiB/1 CPU、随机 loopback 端口、任务标签与清理。没有运行 SG 全套测试。

Gov InstanceSessionClient、SG SessionServer/Manager/Redis/WebSocket/Connected Session/ExecAdapter、SG Resource Validator 与 Resource TerminalValidation/独立 mTLS listener 均为生产代码。PKI 为测试专有：Gov→SG 使用唯一 Gov URI，SG→Resource 使用唯一 SG URI；原 Gov→Resource DNS-only 证书仍独立。Resource 权限/当前意图和原账使用真实 PG，Kubernetes provider/listing/remotecommand 是显式外部替身。

WebSocket 客户端执行 shell，将 marker 写入 task-local 模拟外部 workspace，再在 Stop/Start 后通过新票据读回；原票据停止后、旧版本票据重启后均只收到错误，不能开启 exec。最后产品 Delete、真实 owner 退额与 Acc ENDED 查询仍通过。此工作目录随 SG fixture 生命周期清理，不代表实际 PVC 重挂、删除或 GPU 文件验收。

两次失败限定在 fixture/驱动：外部 shell executor 的 stdin copy 使退出收尾等待，改为显式 StdinPipe；测试将协议数字 exit code 误按字符串解码，改为 raw JSON。已通过的 SG/Res 编译 artifact 在后续仅驱动修改时复用；格式化后的源码与输入哈希不同未被当作新失败重新构建业务代码。生成/格式化继续按白名单回传。真实共享集群、平台身份、前端和远端 main 均未写入。

## 工作盘回收完成条件

`go test -v -count=1 ./internal/data/instance -run '^(TestNotebookMappingAndConditionalStop|TestNotebookWorkspaceReclaimOwnership)$'`、`scripts/instance-integration` 的原四个 PG 用例和 Resource 服务构建均 exit 0，见 [workspace.txt](workspace.txt)。核验绑定 claimRef/PV UID、PVC 消失但原 PV 尚在、Retain/替换 PV 拒绝，以及丢失首次绑定观察后的 exact PVC UID 恢复；Kubernetes API 仍为边界替身。

随后仅在现有生命周期用例追加磁盘观察失败，使用 `INSTANCE_TEST_RUN='^TestInstancePersistentLifecycle$' scripts/instance-integration`，exit 0，见 [workspace-retry.txt](workspace-retry.txt)。原 GPU 退额不被磁盘观察失败阻塞，PVC 删除失败及 PV 未回收仍不能完成整体删除。

因为生产 worker/磁盘观察发生变化，按 [输入文件哈希](workspace-input.sha256) 重新编译 Resource fixture，再复用未变的 Gov/Acc/SG artifact 执行同一具名 HTTP/SG 组合用例，exit 0，见 [workspace-joint.txt](workspace-joint.txt)。没有重跑生成、其他服务构建或无关测试；真实 PG/Redis/mTLS/原账/同步仍完整接通，硬件/Kubernetes/exec 边界仍受控。

[集群只读复查](cluster-preflight.json) 从 Fedora 使用 CA 校验的 HTTPS Kubernetes API，2026-10-13 04:05 CST 再次确认原 API/cluster UID/节点及 GPU 缺项；没有集群写入或凭据输出。独占 PG/Redis fixture 清理后，`docker ps --filter label=ani.instance.fixture` 无本次存留容器。
