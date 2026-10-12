# Governance 全面读写对接（GOV-RESOURCE-20260922）

> 历史租户接入批次。2026-10-12 用户新增授权的平台网络接入及互斥委托范围，以 [当前调用登记](../specs/governance-calls.md#平台网络用户委托) 为准；下列原批范围和历史验收不回写成新接口的证据。

状态：completed（R/V1/G/V2 全部完成 2026-09-23；范围外数据面项已显式 not_verified）。批次：GOV-RESOURCE-20260922。日期：2026-09-23。
governance 侧改动只允许发生在隔离 worktree `/home/ubuntu/Workspace/ani-governance-worktress`。

## ani-system 部署（2026-09-23，main 合并推送后）

- `ani-resource-service` 部署进 `ani-system`：镜像 `anisystem-561e3f2de21e56d8`
  （main `2b94960`，`ANI_NETWORK_MODE=governance`），独立数据库 `ani_resource`
  （显式 `-migrate` 39 表，运行角色 `ani_resource_read` 限定 DML），
  mTLS 复用 `ani-network-server-tls`（root CA `ani-internal-root-ca`，
  server SAN `ani-network-service`），ConfigMap `resource-config` 携带完整配置。
- `ani-governance`（既有部署）升级到合并后 main `91e46bb`
  （镜像 `anisystem-134343a8e014bb50`），`ANI_NETWORK_ADDR` 指向
  `ani-resource-service.ani-system:19090`。启动参数 `-c /app/configs`
  （gowind bootstrap 仅认 `-c` 短参；`-conf` 会被吞成 `onf`）。
- 链路验证：governance Pod → `ani-resource-service:19091/readyz` 200；
  以 governance 同一身份（SAN `ani-governance`、CA `ani-internal-root-ca`）
  经 mTLS 调 `ListVPCs` → 200、0 items（空库，符合"0 条也行"）。
- 已知环境残留（与本批无关）：quota dispatch worker 报
  `sys_quota_operations` 不存在——该库 `gwa_init_20260921` 未跑配额迁移，
  属 QUOTA-GPU-LOCAL-01 的部署事项。

## 执行进度（审计勾选）

- [x] R1 `internal/server/governance.go`：白名单 map（计划矩阵 20 方法）、
  反射租户字段校验（`tenant_id`/`target_tenant_id`）、`x-ani-operator` 头
  （可选，重复/空白拒绝）、EgressCaller 注入（经 `biz.EgressCallerOf` 新增
  只读取器）、`DenyGovernanceStreams` + `DenyVPCStreams` 弃用别名。
  `gofmt`/`go vet` 干净。
- [x] R2 `cmd/ani-resource-service/governance.go`：`runGovernance` 组合根
  （Network+Egress+LB+完整 worker、注册 3 租户 service、不注册平台面）；
  `main.go` 增加 `case "governance"`。`go build ./...` 通过。
- [x] R3 测试：`governance_matrix_test.go`（TestGovernanceAllowlistMatrix）
  + 既有 `TestGovernanceMTLSBoundary`（16 负例）、`TestGovernanceActorNamespaces`
  全部 pass（`go test ./internal/server/ -count=1` → ok）。
- [x] V1 定向验证：`go build ./...` 退出码 0；`go test ./internal/server/` ok；
  `go vet ./cmd/ani-resource-service/ ./internal/server/` 干净。
- [x] G1 `i_network.proto` + `catalog/service/v1/vpc.proto`：新增 10 条 BFF 路由
  与治理面消息（VPC 列表/创建/删除、Operation、EIP 全量、SNAT 查询/绑定），
  buf 1.60.0 build/openapi/aksk-slice 生成通过，产物在 `api/gen/go/`。
- [x] G2 `network_client.go`：`NetworkTenantClient` 出站面（10 方法）——
  `trusted()` 身份断言、`outCall` 元数据重建 + 传输故障分类、`resourceState`
  枚举映射；Egress 走独立 `TenantEgressServiceClient`。
- [x] G3 `network_service.go`：BFF service 10 方法——`trustedOperator` 统一
  身份/租户解析、ID 模式校验（vpc_/eip_/operation UUID）、响应租户断言、
  state/enum 归一化 wire；`vpcProbe` 扩展全方法替身，既有
  `TestNetworkTrustedScope`/`TestNetworkTrustedKeyPrincipal` 不改语义通过。
  wiring_ent.go 改用 `NetworkTenantClient`；`mapNetworkError` 补
  FailedPrecondition → 412 映射。
- [x] G4 登记：`bootstrap-network-access.sql` 扩展 10 条路由 + 权限码
  （循环登记、冲突拒绝、临时表 ON COMMIT DROP）；`interface-integration-register.md`
  追加批次 GOV-RESOURCE-20260922 与 NET-02～NET-11。
- [x] V2 全链路验收（2026-09-23，kind-kind-test / aksk-vpc-20260922）：
  双侧新镜像（resource `govres-20260922-3dda96c81d0784de`，governance
  `govres-20260922-5ca9ae65ac393f06`），`ANI_NETWORK_MODE=governance` 实部署。
  HTTP(NodePort) → governance 闸门 → mTLS → resource → PostgreSQL 全链，
  **17/17 PASS**（读：ListVPCs/GetVPC/GetEIP/ListEIPs/GetOperation；
  写：CreateEIP 落库 provisioning + operation、幂等重放同 ID；
  负向：跨租户 404×3、坏 ID 400×2、未登录 401、不存在 404、无套餐 403、
  SNAT 绑定被基础内网闸门正确拦截且不产生 binding 行；legacy AK/SK 同实例回归）。
  证据：evidence/govres-v2-results.json、验收脚本
  scripts/aksk-lab/govres_v2_acceptance.py。
  not_verified（本批范围外）：SNAT 绑定成功路径与 EIP 供给完成态
  （依赖真实 Intranet 池/VPC 基础内网连接与 kcn 数据面）、LB 接口 HTTP 链
  （BFF 未登记 LB 路由，协议面已由白名单单测覆盖）、平台面（设计性拒绝，
  由单元矩阵覆盖）。执行中修正（保留原文）：拦截器对 GetOperation 需注入
  EgressCaller（修复后 200）；NewWorkerServer 不接受显式 nil variadic
  （nil 展开成单元素 nil 接口导致 worker lane panic，已修复并验证）；
  mapNetworkError 补 412；lab 只读角色按 full-mode 语义授予 DML；
  SA RBAC 授予 kcn CR 权限（lab-only）。

## 目标与范围

将 governance ↔ resource 对接从单一 `GetVPC` 只读扩为**租户面读+写全量**，
使用统一 `governance` 运行模式。本批不做：

- Compute/Storage 域（本计划确立的"域=白名单组"结构为它们预留，不预建目录）。
- 跨租户委托（`DelegatedTenants` 留空，`target_tenant_id` ≠ principal 租户一律 PermissionDenied）。
- PlatformNetworkService 任何方法（平台面非治理受众）。
- 改变既有兼容身份：`vpc-read` 模式、`network.v1` RPC、`ANI_NETWORK_*` 前缀、
  SAN `ani-network-service`/`ani-governance`、actor 头格式（ADR-0006 不变量）。

## 设计决策（已与用户确认）

1. **一种模式，按域分组白名单**。新增 `ANI_NETWORK_MODE=governance`；
   `vpc-read` 保留不删。governance 接入的单元是"方法白名单组"，不是新通道。
   身份信任层（TLS/SAN/三头/principal）域无关、全量复用。
2. **EIP/SNAT/LB 不合并进 NetworkService**。三条 proto service 保持独立
   （信任模型、Public 可见性过滤、领域词汇各自独立）；合并发生在 governance
   BFF 层（统一 `/api/v1/networks/...` 路由空间，内部分发）。
3. **第一批 = 读 + 写**。租户面写接口（Create/Delete/Bind/Set/Update）全部放行，
   `idempotency_key` 透传。
4. **governance 模式装配完整 worker**（与 full 同源），避免"governance 创建的
   资源永远 provisioning"假死；KC 依赖随之回来，部署按完整模式准备。
5. **Attribution 方案 A**：governance 新增出站头 `x-ani-operator`（真实 operator，
   如 `gov-user-7` / `gov-ak-42`），resource 侧派生 Attribution。`x-ani-actor`
   格式合同不变。
6. **EgressCaller 注入**：governance 模式拦截器作为 proto 注释指定的 trusted
   inbound adapter，经 `WithEgressCaller` 注入 principal 派生 caller；
   `PlatformAdministrator=false`。

## 白名单矩阵（resource 侧唯一权威表）

| service | 方法 |
|---|---|
| NetworkService | GetVPC、ListVPCs、GetSubnet、ListSubnets、GetOperation |
| TenantEgressService | CreateEIP、GetEIP、ListEIPs、DeleteEIP、BindVPCSnat、GetVPCSnat、GetVPCSnatBinding、SetVPCSnatEnabled、DeleteVPCSnatBinding |
| TenantLoadBalancerService | CreateLoadBalancer、GetLoadBalancer、ListLoadBalancers、UpdateLoadBalancer、DeleteLoadBalancer、GetLoadBalancerOperation |
| PlatformNetworkService | （无——全部拒绝） |
| 流式 RPC | （全部拒绝，沿用现有 Deny） |

租户字段统一校验：`tenant_id` 或 `target_tenant_id`（若存在）必须等于 principal 租户。

## 工作包

### R（resource 侧，本仓）

- R1 `internal/server/governance.go`：
  - 白名单 map（按上表）替代 `GetVPCMethod` 单常量；
  - 通用租户字段校验（反射 `tenant_id`/`target_tenant_id`）；
  - `x-ani-operator` 头解析（可选头：出现时必须单值且非空）；
  - 拦截器对 Egress/LB 域方法注入 `WithEgressCaller`（注入点在 trusted adapter 层，
    经 service 层包装，不让 server 直接 import biz——实现时以最小包依赖为准）；
  - `DenyVPCStreams` 更名 `DenyGovernanceStreams`，保留旧名别名一个版本周期。
- R2 `cmd/ani-resource-service/governance.go`：新组合根 `runGovernance`——
  Network + Egress + LB 装配、worker 完整装配（同 full 源）、注册 3 个租户 service、
  mTLS/observability/admin 照 vpc_read 模式；`main.go` 增加 `case "governance"`。
- R3 测试（审计要求，全量负向）：
  - 白名单矩阵：每方法正例（租户匹配）+ 每域写方法负例 + Platform 全负例
    + stream 负例 + 未知方法负例；
  - 租户校验：`tenant_id` 伪造 / `target_tenant_id` 伪造 / 缺失；
  - operator 头：缺失（允许）、重复（拒绝）、空值（拒绝）；
  - EgressCaller 注入后 biz 层拒绝路径（DelegatedTenants 空 → 跨租户 PermissionDenied）；
  - 现有 `TestGovernanceMTLSBoundary`、actor、rename 兼容测试全量保留并通过。

### G（governance 侧，仅隔离 worktree）

- G1 `i_network.proto`：补读写 BFF 路由（照 GetVPC 风格，`/api/v1/networks/...`），
  `catalog.service.v1` 补消息（治理面裁剪字段）。
- G2 `network_client.go`：新增方法（照 GetVPC：三头重建、timeout、
  DeadlineExceeded/Unavailable 分类）+ `x-ani-operator` 出站头。
- G3 `network_service.go`：BFF service + `mapNetworkError` 复用 + 响应租户断言。
- G4 登记：`bootstrap-network-access.sql` 扩展（path/权限码族
  `network:vpc:*`/`network:subnet:*`/`network:eip:*`/`network:snat:*`/`network:lb:*`）；
  `interface-integration-register.md` 追加批次 GOV-RESOURCE-20260922。

### V（验证，分两层）

- V1 resource 定向：`go test ./internal/server ./cmd/...`；
  vpc-read-probe 式真实 TLS 探测（扩 `-method`）对新组合根正/负例。
- V2 全链路：独立 namespace（kind-kind-test）、空库、`governance` 模式部署、
  Python 客户端打读写路径；负向（未登记路由 403、跨租户 404/403、
  伪造头 401、平台面 403）各一遍。证据归档两侧
  `docs/execution/records|evidence/GOV-RESOURCE-20260922/`（resource 侧用小写
  `governance-resource-20260922` 延续现有目录风格）。

## 顺序

R1 → R2 → R3（本仓全部，测试绿）→ V1 → G1–G4 → V2 → 双侧记录归档。

## 审计要求

- 每个 R/G 包完成即在本文件勾选并附测试名/退出码；不后补。
- 失败记录保留原文，不抹平为"从未失败"。
- 凭据、SK、私钥不入库不入文档；验收输出仅含 case/status/code。
- 白名单矩阵表是唯一权威；代码、测试、文档三处不一致以表为准并修代码。
