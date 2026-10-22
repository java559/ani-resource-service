# KCN 网络流程草案与最新关联源码核对

日期：2026-10-21。审查对象：[流程草案及两张图](../../specs/kcn-v09-underlay-flow.md)。本记录是审查证据，不是新的正式规格或执行台账；本轮不修改方案正文、应用代码、数据库、运行配置或集群。

## 总判断

**需要修订，管理员“创建虚拟交换机 → 创建二层网络”的主方向保留。** 必改项为：R1 拆开 LB 创建与激活；R2 补管理员 VPC CIDR 候选及租户选择；R3 标明基础连接 rollout 前置；G1 新交换机列表按 Gov 新公共分页合同设计。另需补 G2 的未绑定交换机 DTO/权限边界、K1 的按对象就绪条件与 I1 的交付版本矩阵，避免按旧合同直接进入实现。

本轮已经核实 Gov、Res、installer 的远端分支。**KCN GitLab 远端最新未确认**，只能以明确标为缓存的源码和 installer 锁定物料核查协议；不把本报告称为“所有仓库最新与线上均通过”。

## 固定来源与核查边界

| 来源 | 固定版本与新鲜度 | 本轮状态 |
|---|---|---|
| Res 草案基线 | `d32c4f438b43b8cb7534486c73e116e4d26c890a`，也是本地 checkout HEAD | 已与远端新版本比较 |
| Res 远端默认分支 | `main`，`6bff99f1747a495fe4b278844c20ee72b1ce2ffc`，提交日期 2026-10-20 | **已通过远端 ls-remote 验证并 fetch 固定对象** |
| Gov 远端默认分支 | `main`，`323eb749b8a3d607b26687797b372919052895c7` | **远端 ls-remote 确认，本地 clean HEAD 与其相同** |
| Gov 先前选择的分支 | `codex/gov-model-inference-latest-20261020`，`05bdb9d890b7e89645dd67ed1c697af2ad7c3ed0` | 已确认远端 tip；不是最新 main，不确认当前运行镜像 |
| KCN | 本地缓存 `origin/main=2b9467c66dc9024fbf321a4b3a98a9e5f3fef377` | **远端新鲜度未确认**；相对草案部署基线 ca06a4e 仅 docs/usage.md 变化 |
| installer 远端 main | `78d8418307b6156954feb10aee8d6972d2ff1bbd` | **远端 ls-remote 确认，已 fetch 固定对象** |
| installer 远端 release | `8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166` | **远端 ls-remote 确认，已 fetch 固定对象** |
| 线上集群与历史回执 | 本轮未查询集群；既有出网回执保持历史时点语义 | **NOT_RUN** |

Res 的 GitHub SSH 22 通道首试失败后，使用既有 SSH 443 通道取得默认分支及 SHA。实际执行：

```text
git -c core.sshCommand='ssh -o BatchMode=yes -o ConnectTimeout=10' ls-remote --symref ssh://git@ssh.github.com:443/zhangzhe-ctrl/ani-resource-service.git HEAD refs/heads/main refs/heads/master
  ref: refs/heads/main HEAD
  6bff99f1747a495fe4b278844c20ee72b1ce2ffc HEAD
  6bff99f1747a495fe4b278844c20ee72b1ce2ffc refs/heads/main
git -c core.sshCommand='ssh -o BatchMode=yes -o ConnectTimeout=10' fetch --no-tags ssh://git@ssh.github.com:443/zhangzhe-ctrl/ani-resource-service.git 6bff99f1747a495fe4b278844c20ee72b1ce2ffc
```

仅获取 Git 对象到 FETCH_HEAD，未修改 remote、分支或 checkout，未 reset/merge。源码通过 `git show` 和固定 SHA 的范围 archive 只读检查，临时展开位置为 `/tmp/res-network-flow-review-6bff99f`。本地已有修改包括 `CONTEXT.md`、`docs/START-HERE.md`、`docs/execution/status.md`、`docs/specs/vpc-snat.md`，以及原有未跟踪证据目录与本草案，均保留。下文的 GitHub 链接固定在审查 SHA，不能用仍停留旧 HEAD 的工作树行号代替最新源码。

Res 范围：`api/network/v1`、`internal/biz/network`、`internal/data/network`（含 queries/sqlcgen）、`internal/service/network`、`internal/server/governance.go`、`internal/conf/v1`、`configs`、`migrations`、相关 RBAC，以及 Instance 消费 Network 的接线。本轮仅静态源码核对，没有生成、编译、测试或真实流量复验。

## Res 结论：主方向可保留，租户图和管理员前置需要补齐

虚拟交换机平台管理、名称作为唯一用户业务输入、二层网络单次受理并后台登记网卡，仍是**目标设计**。最新 Res 没有实现这些接口或字段，不能把此事实本身判成方案错误。真正需要按新代码调整的是 LB 创建与激活的区别、VPC CIDR 策略和基础连接 rollout 前置。草案固定来源也应从旧 Res 基线补上最新 SHA；这属于证据更新，不表示本轮已经升级运行版本。

### R1. 租户图拆开 LB 产品创建与后端激活

草案 §4.1 把工作负载接入作为“创建私网 LB”的前置，又把合法后端作为“创建公网或双入口 LB”的额外依赖。最新代码已允许空后端/无可投影监听配置先持久保存 LB，状态为 `waiting_for_backends`，并在同一受理事务保留父引用和 EIP/VIP 独占；此时没有运行投影，不要求真实后端或新鲜的 LB runtime capability。后续配置后端使运行投影非空时，才强制核验 VPC 基础连接、LB capability、Subnet/EIP 和后端身份。

因此应在图中分开“创建 LB、预留入口地址，等待后端”和“配置监听器/合法后端并激活”；工作负载与 LB 可先后或并行准备，汇合后才部署和验证业务。空配置受理成功不能被画成公网入口已经工作，等候阶段 EIP 也不能被画成仍可供 SNAT 绑定。

证据：Res `6bff99f` 的 [API 契约说明及字段](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/api/network/v1/load_balancer.proto#L200-L222)、[按投影决定 runtime 准入](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/load_balancers.go#L249-L269)、[激活时的基础连接及 capability 门禁](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/load_balancers.go#L316-L340)、[等待状态与 EIP 独占受理](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/load_balancers.go#L467-L495)、[仅数据库操作完成后停放 reconcile](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/load_balancers.go#L544-L551)、[运行投影剔除无后端监听器](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/biz/network/load_balancer_deferred.go#L28-L40)。

### R2. 管理员图补 VPC CIDR 候选配置，租户创建先获取可选候选

最新 Res 不接受任意合法 RFC1918 CIDR：首次 CreateVPC 必须精确匹配管理员配置的 `network.vpc_cidr_presets`；配置为空则首次创建拒绝，旧 key 的精确重放先返回原快照。`ListVPCCIDRPresets` 会按当前平台网络事实和已受理地址池过滤候选，平台池创建也反向拒绝与已受理 VPC 重叠。只有“默认内网池验收”不足以完整表达管理员为新租户 VPC 准备的前提。

建议在管理员图加入与地址池配置并行的“配置/发布 VPC CIDR 候选”；租户图在“创建 VPC”处注明从候选选择。正文补充配置发布需要重启 Resource、事实过期 fail closed、与平台池双向不重叠，不扩展为区域/AZ 或跨集群调度。

证据：Res `6bff99f` 的 [配置字段与重启语义](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/conf/v1/conf.proto#L35-L51)、[配置示例默认空列表](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/configs/config.yaml#L11-L18)、[CreateVPC 首次准入](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/postgres.go#L125-L153)、[候选读取及过滤](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/vpc_cidr_presets.go#L52-L91)、[事实新鲜度与网段冲突](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/biz/network/vpc_cidr_presets.go#L37-L53)、[平台池反向检查 VPC](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/platform.go#L282-L299)。

### R3. 自动基础内网明确为 rollout 已启用的目标路径

租户图的“创建 VPC → 系统自动配置基础内网 EIP/SNAT”是正确的目标主链，但最新现有实现依赖持久 `network_connectivity_rollout.new_vpcs_enabled`；默认 false，只有打开后才在 CreateVPC 的同一事务固定基础 EIP/Snat、池和关系。配置默认 Intranet Pool 与打开新 VPC rollout 是两个不同动作，不能写成“建好默认池就必然自动编排所有新 VPC”，也不能宣称历史 VPC 自动补齐。

建议管理员基础内网分支注明“验收合格池并按既有 rollout 流程启用新 VPC 基础连接”；正文将存量 VPC 补齐和聚合切换保留为既有独立机制。无需给租户增加基础地址/SNAT 操作，也无需把 rollout 内部实现细节塞入租户表单。

证据：Res `6bff99f` 的 [CreateVPC rollout 分支](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/postgres.go#L188-L203)、[基础资源固定及受理](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/base_connectivity.go#L17-L79)、[rollout 默认关闭](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/migrations/0006_vpc_base_connectivity.sql#L158-L164)、[持久开关 SQL](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/queries/base_backfill.sql#L80-L87)。本轮不确认线上该开关状态。

## 仍可保留的 Res 设计与既有事实

| 草案内容 | 最新源码判断与证据 |
|---|---|
| 无区域/AZ、部署定位不等同区域 | 可保留。固定范围搜索未发现 Network 区域/AZ 业务字段；现有 [Placement](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/postgres.go#L25-L44) 和 [配置](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/conf/v1/conf.proto#L35-L51) 仍只有集群/namespace 定位。虚拟交换机名称、范围、生命周期属于待新增目标，不冒充既有代码。 |
| 单集群实现不扩展任意集群入口 | 可保留。Postgres 固定单一 Placement；InstanceNetwork 也校验固定 clusterID 与租户 namespace，[消费接线](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/instance/network.go#L12-L45)。新增 cluster_id 必须验证当前可达配置，不能只在 body 接受后继续用固定连接写另一个集群。 |
| KCN v0.9 适配仍需解除旧门禁 | 可保留。`kc_node_facts.go`、`kc_egress.go`、`platform_worker.go` 与旧草案 Res 基线相比未变：旧 [managed_netdevs 注解](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/kc_node_facts.go#L19-L21)、[managedDevices 写入](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/kc_node_facts.go#L294-L326)、[VLAN 仅 devName/vlanID](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/kc_egress.go#L220-L230)、[Device available 前置](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/platform.go#L256-L269) 仍存在。草案已把它们列为待改，不应重复把 NOT_IMPLEMENTED 当新设计错误。KCN 自身结论仍须其固定源码确认。 |
| Proto 不复用 device_id 字段号、幂等 v1/v2 分离 | 可保留。最新 [CreateVlanNetworkRequest 第 3 字段](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/api/network/v1/egress.proto#L226-L237) 没变；[platformReplay](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/platform.go#L151-L169) 和 [迁移约束](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/migrations/0005_vpc_egress.sql#L133-L145) 仍固定 v1，新方案必须增量兼容。 |
| 无 KCN 对象不伪造 Provider UID，内部设备与外部 VLAN 观察分离 | 可保留且仍需实施。旧 [poolReady 通用资源 UID 门禁](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/platform.go#L471-L489) 包括 device，必须改为登记证明与物理接管证明分层；草案已经明确了此差异。 |
| 内部设备安全退役、跨交换机活动唯一及墓碑 | 可保留为目标。当前仍是 [永久 cluster/device 唯一、VLAN 外键与活跃 VLAN 槽位](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/migrations/0005_vpc_egress.sql#L42-L66)，[DeletePlatform 仍拒绝 device 删除](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/platform.go#L525-L541)。草案已要求新内部退役、未知写入核清和迟到 worker fencing，不是删除现有合同。 |
| Node 标签写入属于新增权限与受控适配 | 可保留为实施项。最新 [egress ClusterRole](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/deployments/egress/network-rbac.yaml#L21-L40) 对 Node 仅 get/list/watch；patch 只有 kcn-config 和 Snat。不能声称现有身份已获 Node patch。草案已列新增 Node patch 和仅合并产品组键的约束；本轮不修改 RBAC。 |
| Public EIP 独立申请、同一 EIP 的 SNAT/LB 目标排他 | 可保留。EIP [受理依赖默认 Public 池](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/egress.go#L95-L148)，不要求先有 VPC/工作负载；[Public SNAT](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/egress.go#L282-L314) 仍需基础连接和真实父身份，LB [创建即预留 claim](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/load_balancers.go#L478-L495)。修图时不破坏该独占边界。 |
| 系统默认 VPC 独立平台用例、现有原生资源不自动纳管 | 可保留。现有 [BindVPCSnat](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/egress.go#L230-L244) 锁定 tenant VPC/EIP，[placement/UID](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/6bff99f1747a495fe4b278844c20ee72b1ce2ffc/internal/data/network/egress.go#L305-L314) 也须同租户放置，不能伪造系统 kcn-cluster 为租户产品。原生对象的精确 UID 与运行状态本轮未复验。 |

## Gov：新增合同要按当前规则接入

### G1. 新交换机列表不能继续设计为 limit/cursor

草案 §5.2 明写新交换机列表沿用 limit/cursor，但 Gov 当前[前端公共合同](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/docs/contracts/frontend-http-api.md#L22-L81)要求新增/修改列表使用 `page/pageSize`、直接返回 `items/total`，支持直接跳页。`total` 在 HTTP JSON 中为十进制字符串；不能用前端扫描 cursor 全量结果伪造页码。

既有平台 VLAN/Gateway/Pool 仍有 [limit/cursor 存量合同](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/docs/contracts/platform-network.md#L13-L21)，[实际策略白名单](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/pkg/middleware/auth/platform_network_policy.go#L143-L153)也尚未迁移。修订应明确：新虚拟交换机列表遵守新规范；旧接口是否迁移另设范围和兼容窗口；完整网卡事实仍不伪造分页。这不是本轮擅自改变全部旧接口的授权。

### G2. 未绑定交换机不能直接复用现有 PlatformResource 校验

平台网络仍是旧 27 RPC，[创建 VLAN DTO](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/api/protos/catalog/service/v1/platform_network.proto#L173-L181)只接受 device_id 等旧字段，[BFF 映射](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/service/platform_network_service.go#L443-L464)亦然。与先前部署分支 05bdb9d 相比，platform-network Proto/Service/client/policy/权限 SQL 的相关源码无变化；最新 main 没有自动补上虚拟交换机。

草案要求先仅存名称、到二层网络才绑定集群，这个产品设计可以保留，但现有 [validPlatformResource](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/service/platform_network_service.go#L68-L98)只接受 device/vlan/egw/pool，且必须有 cluster_id 和 configuration。实施合同应明确使用支持未绑定状态的 VirtualSwitch DTO/validator；不能塞假 cluster_id、fake Provider UID 或套用错误 kind 通过旧校验。

新入口还需同步 `data` 请求包装、路由/方法白名单、产品 ID 校验、独立动作权限和受信 RPC 清单。当前 [平台授权](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/service/platform_network_service.go#L53-L65)及 [27 项动作策略](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/pkg/middleware/auth/platform_network_policy.go#L11-L52)不自动允许新接口；[内部调用](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/data/platform_network_client.go#L14-L31)仍重建 platform scope、用户 actor 与 request ID，不携带租户 UUID。

### G3. 固定 Provider API 后再更新 Gov 依赖

Gov [go.mod](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/go.mod#L27-L31)当前固定 Resource `v0.0.0-20261019174951-733f625995f8`，不等于本轮核实的最新 Res main。新交换机和二层网络 v2 必须先正式发布 Res API，再由 Gov 固定可追溯模块版本、更新 DTO/客户端及鉴权；不通过 go.work/local replace 或前端多请求串联绕过发布和受理边界。

R1/R2 的 Gov 侧已有相应接线：LB [空数组映射](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/service/network_service.go#L604-L612)不会拒绝零后端，[创建映射](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/service/network_service.go#L713-L743)保留 listeners/backends；[ListVPCCIDRPresets](https://github.com/zhangzhe-ctrl/ani-governance/blob/323eb749b8a3d607b26687797b372919052895c7/app/admin/service/internal/service/network_service.go#L895-L914)已代理候选读取。此为静态接线结论，不代表本轮已跑通最新组合。

## KCN：协议可保留，远端最新存在核查缺口

本地 GitLab SSH 被关闭，既有 Fedora 路径 SSH banner 超时，HTTPS 只读探测 TLS unexpected EOF；本轮已停止重试。以下结论只针对缓存 `2b9467c` 和与其运行源码相同的旧基线 ca06a4e，不推断未知远端或当前集群。

组标签/注解、allocated、immutable vClusterName/devName/vlanID、优先级用途与删除引用保护没有可见协议变化；缓存与 ca06a4e 之间只有 docs/usage.md 差异。管理员“交换机 → 二层网络”、后台登记和不引入区域/AZ 的方向可保留。

### K1. 保留恢复前置，并明确按对象观察状态

组不存在时 VLAN 初始化不可靠重试，拓扑索引仅覆盖 Initialized VLAN；网卡缺失/已有 IP 等稳定配置错误仍可能不重试。草案已要求的组识别完成/安全重驱协议不能移除。直接依据是缓存 [VLAN 校验与事件过滤](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/networking/vlannetwork_controller.go#L183-221)、[只索引 Initialized VLAN](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/handlers/indexer.go#L62-76)、[daemon 稳定错误](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/daemon/networking/vlannetwork_controller.go#L213-225)。

草案已有物理接管联合证明，建议在状态合同中进一步固定以下差异，避免统一 wait Ready：

| Provider 对象 | 当前缓存源码的完成依据 |
|---|---|
| VLAN | Valid/Initialized、完整组成员、notReadyNodes 与真实 OVS；控制器不发布 Ready/Healthy |
| Gateway | 有实际 Ready，另核身份和 localIP 等既有依赖 |
| Underlay Subnet | Ready/underlayState 及依赖；underlayState.ready 镜像 VLAN 摘要，不代表独立流量验证 |
| Snat/EIP | Snat Valid/phase，EIP boundResource 的类型、名称、VPC、启用状态和 ObservedGeneration 对应 Snat generation；不等待 Snat Ready/Healthy |

来源：缓存 [VLAN 汇总](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/networking/vlannetwork_controller.go#L225-255)、[Subnet underlayState](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/networking/subnet_controller.go#L877-892)、[Snat 状态](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/networking/snat_controller.go#L111-138)、[绑定代次](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/handlers/eip_bound_res.go#L114-125)。

## Installer：补版本矩阵与运行前置，不增加产品步骤

### I1. main、release 与旧运行回执必须分列

installer 最新 main 78d8418 的 [components.lock](https://github.com/zhangzhe-ctrl/ani-installer/blob/78d8418307b6156954feb10aee8d6972d2ff1bbd/kubekey/ani/components.lock.yaml#L35-L41)仍锁 dev/fix2；最新 release 8df08cd 的 [锁文件](https://github.com/zhangzhe-ctrl/ani-installer/blob/8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166/kubekey/ani/components.lock.yaml#L142-L159)和 [materials](https://github.com/zhangzhe-ctrl/ani-installer/blob/8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166/kubekey/ani/kcn/materials.lock.json#L1-L26)已固定 KCN source 2b9467c、index digest 494432d2…、amd64 digest 26470989…与 17 CRD。旧 10–12 回执记载 ca06a4e，本轮未查询其实际 imageID/CRD；三者不能合称已部署的最新 v0.9。

实施前应固定实际运行的 CNI profile、imageID/CRD，再选对应适配和完成条件；不因拉了新 installer 分支就宣称旧集群已升级。

### I2. 首装 KCN 就绪不提供 Res 新功能的权限与交付

release 的 [安装任务](https://github.com/zhangzhe-ctrl/ani-installer/blob/8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166/kubekey/builtin/core/roles/ani/kcn/tasks/main.yaml#L17-L79)落地 CRD、KCN runtime/RBAC 并等待控制器/daemon；[Node patch 权限](https://github.com/zhangzhe-ctrl/ani-installer/blob/8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166/kubekey/builtin/core/roles/ani/kcn/templates/install.yaml#L70-L84)属于 KCN 身份，不属于 Res。

该 [执行合同](https://github.com/zhangzhe-ctrl/ani-installer/blob/8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166/docs/execution/kcn-full-install.md#L3-L17)面向 20–22 且不部署 ANI 应用。扫描未发现其替 Res 完成 NodeFacts 发布、平台组标签所有权或 Node 标签写入交付。因此 Res NodeFacts、目标集群访问身份、必要 Node patch 与仅合并本产品组键的约束/审计，仍需列为 Res 新流程的运行前置。原方案已有相关待实施项，不能因 installer 更新就删掉。

release [锁定能力](https://github.com/zhangzhe-ctrl/ani-installer/blob/8df08cd5c13f0ce5156a2df3f1ce0ed8cf56f166/kubekey/ani/components.lock.yaml#L142-L159)仍为 NetworkPolicy unsupported；BasicNetworkIsolation 不等于 Kubernetes NetworkPolicy。保留真实 DNS/HTTPS/SNAT 验证，不加入“创建 NetworkPolicy 放行出口”的步骤。

## 验证结论边界

- **PASS（已确认来源）**：Gov、Res、installer 的上述远端分支/SHA与固定源码核对完成；Res/installer 获取对象不改 checkout，Gov clean HEAD 等于已核实远端 main。
- **未确认（来源新鲜度）**：KCN GitLab 远端最新；已核对缓存源码及 installer 明确锁定的物料版本，不扩张为远端最新结论。
- **需修订（文档）**：R1–R3、G1；G2/G3/K1/I1/I2 作为具体接口与交付前置进一步写清。现有方案正文与流程图本轮未改动。
- **可保留（目标设计）**：虚拟交换机平台管理、无区域/AZ、二层网络单次后台受理、身份与占用门禁、平台/租户边界及原生资源默认保留。
- **NOT_RUN**：应用实现、生成、构建、行为测试、数据库迁移、线上权限检查和集群/业务流量验收。此结论不代表最新源码已部署，也不代表目标流程已经跑通。
