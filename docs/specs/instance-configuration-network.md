# 容器实例挂载、配置与 VPC 接入改造草案

日期：2026-10-19。状态：**待审阅的增量设计，尚未实施**。

目标是让用户通过 Gov 创建容器实例时选择已有 PVC 和挂载路径、VPC/Subnet，以及标签、注解和环境变量；配置经过授权、校验、冻结和持久化后进入真实 Notebook/Pod，启停和重建继续生效，最终删除正确处理卷引用和网络关联。

本文只定义新增行为。现有实例、GPU 执行和原账退额规则继续引用[实例规格](instance-mvp.md)，网络资源所有权继续引用 [ADR-0001](../adr/0001-own-network-lifecycle.md)，租户数据约束继续引用 [ADR-0002](../adr/0002-use-tenant-owned-data-without-rls.md)。实施顺序见[改造计划](../plans/instance-configuration-network.md)，实际交付结果只记入[执行状态](../execution/status.md)。

本草案经确认并实施时，将定点替代原实例规格中“工作盘固定路径、不引入已有卷”和“仅复用现有网络”的范围限制；本次不把旧规格提前改写成已交付能力，也不授权部署、跨仓库代码修改或 kcn 修改。

## 1. 业务主链和当前断点

```mermaid
flowchart LR
  U[用户选择卷 网络与配置] --> G[Gov 当前授权和幂等受理]
  G --> R[Res 解析生效配置]
  R --> F[Gov 冻结命令与持久投递]
  F --> D[Res 持久接受与后台执行]
  D --> A[准备网络关联和卷引用]
  A --> N[创建 Notebook 和 Pod]
  N --> V[核验实际挂载 配置和网络]
  V --> S[终端使用 启停与重建]
  S --> X[删除计算 释放网络与卷引用]
```

首个可观察结果：正式 Gov 创建一个 CPU 实例，两个已有 PVC 挂载到指定路径，自定义环境变量可在容器读取，标签/注解出现在实际 Pod；启停后数据和配置保留；删除后用户 PVC 保留、系统工作盘回收。下一条纵向链在此基础上指定 VPC/Subnet，验证取址、通信、Pod 自动重建及最终网络释放。GPU 继续走同一配置实现，并单独验证执行参数和原账退额。

核查快照：Res `f7459f7ae71c5573f53a50c5d20908b129c17e0c`；Gov `90b1ecc8cfa31b96ac43a8e4ff3aa18d41f03812`；用户提供的 kcn 本地 checkout `2b9467c66dc9024fbf321a4b3a98a9e5f3fef377`。以下是源码事实，不是部署或数据面验收。

| 断点 | 固定源码落点 | 对改造的影响 |
|---|---|---|
| 创建没有用户卷、网络或 metadata/env 字段 | [Res Proto](../../api/instance/v1/instance.proto)、[Gov Proto](https://github.com/zhangzhe-ctrl/ani-governance/blob/90b1ecc8cfa31b96ac43a8e4ff3aa18d41f03812/api/protos/instance/service/v1/instance.proto#L13-L23) | 两端合同、解析及冻结必须一起演进 |
| 工作盘和工作目录固定 `/workspace`，Pod 校验也检查该路径 | [Notebook adapter](../../internal/data/instance/notebook.go)，RenderNotebook、verifyPod | 不能只修改渲染，必须同步运行详情、日志和终端目标核验 |
| 专属工作盘会被最终删除 | [工作盘实现](../../internal/data/instance/notebook.go)，EnsureWorkspace、observeWorkspace、DeleteWorkspace | 用户已有卷不能冒用专属工作盘字段和清理流程 |
| Res Attachment 拒绝不同 Pod UID | [Confirm](../../internal/data/network/attachment.go)、[worker](../../internal/biz/network/attachment_worker.go)、[kc adapter](../../internal/data/network/kc_attachment.go) | 普通 Notebook 启停和自动重建需要受控替换合同 |
| kcn 有新 Pod 创建网卡及旧 sandbox 清理路径 | [Pod controller](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/controller/core/pod_controller.go#L472-500)、[CNI handler](https://gitlab.changqingyun.cn/kubercloud/sdn/kc-networking/-/blob/2b9467c66dc9024fbf321a4b3a98a9e5f3fef377/internal/daemon/cniserver/handler.go#L299-355) | 本次 UID 限制的修改对象是 Res；保留 kcn 的对象归属和迟到 DEL 保护 |
| 当前规范化使用 EmitUnpopulated，命令摘要显式列举字段 | [business.go](../../api/instance/v1/business.go) | 直接追加字段会改变旧摘要，需要按保存版本解释请求和冻结载荷 |
| 实际 governance 装配没有实例网络关联闭环 | [governance composition](../../cmd/ani-resource-service/governance.go)、[full composition](../../cmd/ani-resource-service/app.go) | 两种支持实例的运行路径均需装配 Attachment worker 和实例 Consumer |

## 2. 范围和所有权

本次支持创建时配置，CPU/GPU 共用。创建后的配置冻结；后续在线修改、换网、扩盘、卷创建/快照/克隆不属于这次增量。标签/注解/env 若以后需要修改，再定义带版本检查的更新行为。

| 所有者 | 本次职责 |
|---|---|
| Gov | 公开入参、当前权限检查、可信租户映射、幂等受理、冻结命令及前端合同；不直接读 Kubernetes 或管理网络表 |
| Res Instance | 配置解析、PVC 引用、Notebook/StatefulSet/Pod 身份、启停和最终封闭、实例状态及恢复 |
| Res Network | VPC/Subnet 准入、Attachment、网络投影、独立观察、地址资格和真实释放 |
| kcn | 按受管网络投影建立/回收 VNic、VNicIP、Nic 和底层数据面；当前方案不要求修改其源码 |
| ACC/Gov 原账 | GPU 执行及原始计费规则继续使用现有合同，不将用户 metadata 或网络观察混入 GPU Execution |
| 前端 owner | 卷候选、VPC/Subnet 选择、键值输入和状态展示；本次方案提供合同，不修改前端仓库 |

Instance 在 `internal/biz` 声明自己需要的窄 Network 和卷查询 Interface；Adapter 在 `internal/data`，composition root 注入现有 Network 用例。Instance 不导入 Network repository、sqlc 或 Provider，不跨域读表。Network 的反向 Consumer 通过实例公开只读用例取得提交事实。

## 3. 创建输入和查询合同

### 3.1 公开字段

保留现有 `POST /api/v1/instances` 和 `data` 外壳。以下为新增字段提案；`idempotency_key/name/image_id/image_scope/preset_id/scheduling` 沿用原合同。

```json
{
  "data": {
    "idempotency_key": "10000000-0000-4000-8000-000000000001",
    "name": "demo",
    "image_id": "img_example",
    "image_scope": "tenant",
    "preset_id": "10000000-0000-4000-8000-000000000002",
    "workspace_mount_path": "/work",
    "volume_mounts": [
      {"pvc_name": "datasets", "mount_path": "/data", "read_only": true},
      {"pvc_name": "outputs", "mount_path": "/outputs", "read_only": false}
    ],
    "network": {
      "vpc_id": "10000000-0000-4000-8000-000000000003",
      "subnet_id": "10000000-0000-4000-8000-000000000004"
    },
    "labels": {"team": "ml"},
    "annotations": {"example.com/purpose": "training"},
    "env": [{"name": "APP_MODE", "value": "dev"}]
  }
}
```

示例 ID 是占位值，实际请求必须使用可选目录返回的镜像、规格、卷和网络。

| 字段 | 缺省及验证 |
|---|---|
| `workspace_mount_path` | 缺省 `/workspace`；显式值须为规范绝对路径，并同时成为主容器 workingDir |
| `volume_mounts[]` | 缺省空；服务器生成 volume name，客户端不提供 namespace、cluster、UID 或卷删除策略 |
| `network` | 缺省沿用当前集群网络行为；提供时 vpc_id/subnet_id 均必填，解析为显式 VPC 接入，失败不退回默认网络 |
| `labels`、`annotations` | 缺省空；字符串 map，校验 Kubernetes 格式及本方案保留键 |
| `env[]` | 缺省空；有序 name/value 数组，允许空字符串，拒绝重名；首版是字面值，不开放任意 valueFrom/envFrom |

显式 `null`、未知字段、无效类型和重复 JSON key 均拒绝。v2 内缺省与空集合视为相同意图；`network: {}` 和空路径非法。路径不自动清洗，环境值不 trim、不展开。环境数组保持输入顺序，`$(VAR)` 的前项引用语义不能被排序破坏。

拟定首版上限：附加卷 8 个、环境变量 64 个、用户 labels/annotations 各 32 项，环境值总计 16 KiB、用户 metadata 总计 16 KiB；保留现有 64 KiB 请求体上限。数值是本草案建议，确认后固定到两端验证及前端提示，不能只在页面限制。

### 3.2 可选目录与配置回显

新增 `ListMountablePVCs` 及批量解析端口，Gov 公开候选入口建议为 `GET /api/v1/instances/mountable-pvcs?page=1&pageSize=20&name=...`。静态路由必须先于 `{instance_id}` 路由匹配。分页沿用 `page/pageSize → items,total`，最大 pageSize=100；Kubernetes LIST 使用一次完整一致的分页快照，完成租户过滤、确定性名称排序后计算真实 total 并切页。内部每页 200、单次最多 2,000 个 PVC；continue 过期重启本次查询，超限返回明确容量错误，不返回截断集合或伪造 total。内部上限同样属于首版建议值。

候选返回 `pvc_name、phase、requested_capacity、access_modes、volume_mode、selectable、unavailable_reason`；容量使用 Kubernetes quantity 字符串，例如 `20Gi`。UID 由 Res 内部冻结，不作为公开可写字段。候选只在可信租户 Namespace 内查询，过滤其他实例专属工作盘及非授权资源。目录展示不能替代创建时重查。

VPC/Subnet 继续复用现有租户列表，前端按 VPC 筛选 Subnet；CPU/GPU 规格解析出的 cluster 必须与所选网络 placement 一致。用户看不到或不能使用的卷/网络不作为可猜测的跨租户详情返回。

新增授权配置查询 `GET /api/v1/instances/{instance_id}/configuration`：返回已保存的生效工作目录、卷挂载、网络产品 ID、用户标签/注解/env；采用实例详情读取权限，明确该权限可以读取字面环境值。列表、操作状态、审计和错误不回显 env 值；运行诊断继续只返回环境变量名称/来源。若产品未来需要 Secret，另定义受控引用和读取权限，不能把字面值字段标成密文存储能力。

实例详情增加独立的 `network_state/network_reason/network_cleaned` 和卷引用状态；无网络选择为 `not_requested`。显式联网实例只有当前 Pod Ready 且 Network 核验 attached 后才展示整体 running；部分就绪保留 pending/reason，暂停后不保留旧 IP 的地址资格。

## 4. PVC 引用和工作目录

### 4.1 两类卷分开管理

系统工作盘继续由实例创建、保存 PVC/PV 身份和最终回收。用户已有卷以 borrowed 引用挂载：停止保留引用，删除实例只解除引用，不删除其 PVC/PV，不给用户卷添加指向 Notebook 的可回收 ownerReference。用户卷丢失不触发自动补造，也不阻挡已终止实例解除该引用。

冻结挂载项包含 `cluster_id、namespace、pvc_name、pvc_uid、mount_path、read_only、ownership=borrowed`。用户输入和服务器解析结果分开保存；重放及 Start 使用原冻结引用，不重新选择同名卷。

### 4.2 校验与竞态

1. 先解析可信租户 Namespace，再检查 PVC 的 Filesystem 模式、归属、未删除及非 Lost；不接受其他实例专属工作盘。
2. 合法 Pending PVC 可以使用，包含 WaitForFirstConsumer；不能要求所有候选先 Bound。绑定、调度及 CSI 挂载问题作为真实状态和原因返回。
3. 挂载路径必须绝对、规范，拒绝 `/`、系统关键目录、重复路径及与工作目录相互遮盖的路径；具体受保护目录集中在 adapter 校验，不提供任意 volume source 或 hostPath。
4. 创建 Notebook 前、Start 前检查原 PVC UID；运行观察核对每个冻结 claimName/mountPath/readOnly。卷替换或访问失败给出明确原因，不静默切换数据。
5. RWO/RWOP/RWX 按实际存储和调度能力解释，不用无锁计数宣称独占。候选可选不保证并发挂载成功；访问模式及占用原因需可见。

Pod PVC 引用只有 claimName，没有 UID 前置条件。应用层 UID 冻结和 GET 复核不能消除“检查后被外部删除并同名重建”的竞态。受支持部署需让 PVC 删除权限/准入尊重使用中的引用；在此保障未就绪时不得声称严格防替换已完成。实现验收须包含同名重建负例及实际授权配置。

### 4.3 持久化和投影

不可变挂载进入 Instance Spec JSON 快照；新增窄引用关系表，按 `(tenant_id, instance_id, mount_path)` 保存原 PVC UID 和 active/released 状态，并对实例使用租户复合 FK。系统 `workspace_bindings` 继续只管理专属盘，不混用 borrowed 引用。跨域 Kubernetes PVC 没有数据库 FK，引用身份由上述校验保证。

RenderNotebook、workingDir、verifyPod 和镜像目录约定一起改为读取冻结值。Notebook 主容器仍名为 `workspace`，避免改变 SG 的容器目标。SG、日志、运行详情继续使用同一 owner/UID 目标核验；不能为接受新挂载而绕过原受管容器验证。

## 5. 标签 注解和环境变量

用户配置与 ANI/GPU/Network 管理字段分别保存。渲染时先验证键名和保留键，再合并；发生控制字段冲突直接拒绝，不静默覆盖。首版保留范围如下，模式匹配按 key 的域和名称解析，不能用容易绕过的任意子串匹配。

| 保留范围 | 控制用途 |
|---|---|
| `ani.io/*`、任意 `*.ani.io/*` | 租户、实例、owner、意图版本、GPU 执行和签名 |
| `kubeflow-resource-stopped`、`notebook-name`、`controller-revision-hash`、`statefulset.kubernetes.io/*`、`apps.kubernetes.io/pod-index` | Notebook 生命周期和控制器选择 |
| `networking.kubercloud.com/*`、任意 `*.networking.kubercloud.com/*`、`k8s.v1.cni.cncf.io/*`、`ovn.kubernetes.io/*` | 网络选择、网卡/IP 引用和关联 |
| `volcano.sh/*`、`scheduling.volcano.sh/*`、`scheduling.k8s.io/group-name` | 受管队列、PodGroup 和设备调度 |

当前部署中具有 admission 注入语义的附加键，在开放写入前按实际配置补入 adapter 的精确保留集合和测试，避免用户标签触发未经授权的配置注入；普通业务 key 仍可自定义。这里不引入通用 YAML 异常注册表。

`ANI_INSTANCE_POD_UID` 及其他实际平台/设备运行时保留环境变量不可由用户覆盖。普通 env 值按原样保序下发；业务校验和审计不输出值。用户标签/注解不进入 ACC `GpuExecution`，不改变其执行摘要或原 charges。

Notebook v1.10 的用户标签/注解写入 Notebook 顶层 metadata，由 controller 传播到 Pod；PodSpec 继续写 `spec.template.spec`，不能写入会被 CRD 丢弃的 `spec.template.metadata`。最终读取实际 Pod 核对用户配置及系统字段都存在，不能只断言 RenderNotebook 的输出。

## 6. Res Network 支持 Notebook 的 Pod 替换

### 6.1 明确关联模式

Attachment 是 Res 的网络消费记录，区别于 kcn 的 VPC/TransitRouter `VpcAttachment` CRD。现有 `single_pod` 模式继续保留固定 Pod UID 和原释放语义；新增内部可信 `workload_mode=controller_managed`、Consumer 协议 v2，显式登记给实例 owner。公开用户仅选 VPC/Subnet，不能提交模式、owner 身份或 UID 来绕过校验。

逻辑 Attachment 固定关联 tenant/instance/submission/generation/cluster/namespace 和原 VPC/Subnet，正常 Pod 重建不创建新的逻辑网络意图，也不自动换网。网络选择不承诺跨重启保留 Pod IP。

### 6.2 提交快照和独立核验

GetSubmission v2 保留 `open/closing/closed、finalization_id`，增加以下可信事实：

| 字段 | 含义 |
|---|---|
| `snapshot_revision` | 实例提交快照的单调版本，和实例 version、lease epoch、观察 generation 分别解释 |
| `desired_run_state` | running/stopped/deleted；停止不是永久 closed |
| `root_controller` | Notebook 的 GVK、namespace、name、UID；首次记录后同 Submission 不静默换根 |
| `controllers` | 已登记的 StatefulSet 身份和父引用 |
| `current_pods` | 当前候选 Pod 的完整引用及 owner 链；单实例最多一个提供有效地址的 Pod |
| `retired_pods` | 曾登记的旧 Pod 身份，仅用于清理核验，不能提供地址资格 |
| `closed_at` | 不再可能创建工作负载、未知创建已查明且控制器已消失后的最终封闭时点 |

Instance 持久登记自己创建和观察到的身份；Network 自己通过 Kubernetes GET/LIST 逐跳复核 Pod→StatefulSet→Notebook 的 GVK/name/namespace/UID、controller ownerReference、租户和关联标签、网络投影。Consumer 的 UID 是待核验声明，不是直接授权。

Network 比 Instance 快照先看到新 Pod 时，保存待核验候选并等待新快照，不仅因 UID 变化就永久 ProtocolBlocked；候选和历史资源均进入清理观察，但只有核验通过的当前 Pod 可以提供地址和 LB 后端资格。错误租户、矛盾 owner 链、根 UID 偷换或不符合单实例策略的并发 live Pod 仍是协议冲突。不能单纯移除现有所有 UID 检查。

Network 持久保存最高已接受 snapshot_revision、固定根 UID、相应快照摘要和 finalization_id。低版本响应只忽略并重试，不回退当前 Pod、生命周期或地址资格；同版本异内容判协议冲突。Confirm 携带 submission/generation、Pod UID 和 expected_snapshot_revision，按前置版本条件更新，重复同值幂等；旧代迟到 Confirm 不能覆盖新代，closing/closed 不能被旧 open 快照恢复。进程重启后继续使用已保存的栅栏。

### 6.3 创建 启停 重建和删除

| 场景 | Instance 动作 | Network 收敛规则 |
|---|---|---|
| 创建 | 本地事务保存网络选择、稳定 request_key/submission/generation；Prepare 后保存 attachment、binding revision 和投影，再创建 Notebook，登记真实 owner 链并 Confirm | Prepare 复用现有同租户、VPC/Subnet 关系及 placement 校验；独立核验后 attached |
| Stop | 持久停止意图，条件停止 Notebook；保留逻辑网络选择，报告 stopped 和实际 Pod 集合 | 无 Pod 是正常暂停；撤销旧地址资格，继续观察旧 VNic/IP 清理，保留 Attachment 占用 |
| Start | 使用原 Spec 和网络投影，登记新 Pod；不重占 GPU 原账 | 验证同一根 owner 链后更新当前 Pod，旧关系进入历史清理 |
| Pod 自动重建 | Notebook/StatefulSet 自行造新 Pod，Instance 更新提交快照 | 未确认候选先等待；通过 owner 链后接替，不依赖每代提前改模板 |
| 删除或放弃创建 | 先保存 closing 和禁止继续发送；删除已知 Notebook，查明未知 CREATE；确认控制器退出后最终 closed，再持久请求 Release | 独立确认当前、历史和待核验候选 Pod/VNic/VNicIP 等关系全部消失后 released |

同一动作使用稳定幂等身份。Prepare/Confirm/Release 丢回执或进程重启后由持久步骤重试，不依赖内存调用链。发出但结果未知的 Notebook CREATE 不能因为一次 404 就 closed 或释放网络；未知关系也不能被重置为“无资源”。

Stop 完成需要确认无运行 Pod；旧网络清理状态独立显示。最终实例删除完成要求计算退出、专属盘清理、borrowed 引用解除、网络 released 和应有退额回执均完成。GPU 在实际计算释放满足现有规则后即可按原账幂等退额，磁盘/网络清理失败保留独立待清理状态，不能重复退额或永久扣留已释放计算的原账。

### 6.4 数据与运行装配

Instance 侧新增 submission/网络绑定记录，保存网络选择、步骤、attachment、投影、controller/Pod 身份、snapshot_revision 和 finalization。Network 侧新增模式、根工作负载引用、当前/历史 Pod 绑定，并使 managed 模式的 Provider 关系以 kind/namespace/name/UID 识别多代对象，不能继续把同名新 UID 一概判冲突。

新关系均带 tenant_id，本域关系使用租户复合 FK；Instance 与 Network 各自事务，不跨域数据库 FK、联表或全局事务。现有 lease/epoch、观察新鲜度、关系索引、持久通知及释放证明继续复用。

composition root 在 `governance`、`full` 实际实例路径装配：Instance Network Adapter→Network Attachments、Network Consumer→Instance 只读提交查询、Attachment worker 和 Provider。既有外部 Consumer 按登记的 owner/协议路由，不因新本地 owner 消费者而丢失。内部调用不通过 loopback gRPC，也不因此放开 Gov 的 Attachment 写 RPC allowlist；已有业务 mTLS、租户身份与方法授权保持有效。

## 7. 摘要版本 旧数据与原账

由服务端确定并持久保存 `instance_contract_version`。旧记录和缺省旧命令为 v1；新功能开启后的新受理为 v2。版本贯穿 Gov 幂等记录、冻结载荷、Res 命令与实例快照，用户不能指定降级版本。

| 数据 | v2 规则 |
|---|---|
| 原始请求意图 | 包括用户输入的全部新增字段；metadata 稳定按键编码，挂载按规范路径排序，env 保持原顺序；固定缺省/空值规则 |
| 生效 CreateSpec | 保存实际工作目录、PVC UID、网络选择、用户 metadata/env 和既有受管字段；默认只解析一次 |
| 命令摘要 | 使用独立 `resource-create-v2` 域和显式字段集合覆盖完整冻结配置；GPU Execution 仍用它原有摘要 |
| 动态观察 | Attachment 投影、当前 Pod、地址和清理进展单独保存，不能回写并改变原 CREATE 摘要 |

Gov 仍先做当前授权，然后按租户/主体/action/幂等键查旧操作，再用旧操作保存版本的固定字段投影比较。合法旧请求直接返回原 operation/resource/charges，不重新 Resolve；旧键携带新增配置必须冲突，不能把未知字段丢掉后误判为同一次请求。新键才解析默认值及外部引用。

首次受理把版本、请求意图摘要、冻结载荷和原有占额记录在同一事务中确定。两个并发请求争用同一幂等键时，未胜出的请求按胜出记录保存的版本重新比较，不沿用自己预先选择的版本。运行开关只决定新受理策略，不能改变存量操作的解释方式。

v1 摘要函数冻结原字段集合及原默认值，不能借当前 protobuf descriptor 和 EmitUnpopulated 重建历史字节。CPU frozen decoder 必须按保存版本校验规范编码及原 business_payload 字节摘要；GPU 继续验证原冻结载荷。历史 JSON、原请求摘要、ChargeID 和原始计费数量不批量重写。

Res 的 decoder 和 AcceptCreate 还必须拒绝“version=1/缺省版本但携带任意新增配置字段”的命令，不能让渲染读取 v1 摘要没有覆盖的值。只保留固定 v1 字段投影不足以完成这项校验；旧冻结载荷必须严格满足旧字段集合，v2 全量配置必须绑定 v2 摘要。

旧实例的 `/workspace` 等兼容生效值在完成 v1 严格验证后由内部版本投影补出，不能作为新增请求字段参与旧摘要重算。

例：T0 创建时缺省路径解析为 `/workspace`，PVC UID 为 U1。T1 平台改了路径默认值，原 key 重放仍返回 T0 的实例和快照；T2 卷同名换成 U2，Start 返回引用已变，不重新选择 U2；T3 原 key 增加新的 env，返回幂等冲突。更换网络、env 或卷必须是新意图，不能通过重试改旧实例。

## 8. 错误 状态和前端交接

以下 reason 是拟新增合同，实施时更新两端错误映射及 OpenAPI，不让依赖错误一律变成 503，也不让新资源上限误映射成日志连接错误。

| HTTP | 拟定 reason | 处理 |
|---|---|---|
| 400 | INVALID_INSTANCE_REQUEST、INSTANCE_RESERVED_CONFIG | 显示字段路径和安全原因；保留键、路径或重复 env 等输入问题 |
| 403 | INSTANCE_SCOPE_DENIED | 当前动作无权限；不回显环境值或他租户资源 |
| 404 | INSTANCE_PVC_NOT_FOUND、INSTANCE_NETWORK_NOT_FOUND | 不存在或不可见的资源统一语义，避免跨租户枚举 |
| 409 | INSTANCE_IDEMPOTENCY_CONFLICT、INSTANCE_PVC_REFERENCE_CHANGED、INSTANCE_NETWORK_CONFLICT | 需要新意图或修正选择；不得自动降级/重选 |
| 422 | INSTANCE_PVC_CATALOG_LIMIT | 候选集合超过受支持的有界查询容量，明确提示；不伪造分页成功 |
| 503 | INSTANCE_DEPENDENCY_UNAVAILABLE | 依赖不可读；同键重试，已受理操作继续由 worker 恢复 |

异步挂载、网络等待及清理失败保存在实例/操作 reason 中，不把一次接受成功显示为运行成功。前端需要展示工作盘与已有卷的删除差异、只读标记、网络准备/暂停/释放状态，以及 PVC/CSI/网络的实际失败原因；敏感环境值只在授权配置视图读取。

## 9. 验收条件

| 编号 | 必须观察到的结果 |
|---|---|
| A1 | 正式 Gov 创建 CPU 实例，两个已有 PVC 挂到指定路径，其中只读卷写入被拒绝；终端实际读写验证，不只检查 YAML |
| A2 | 自定义工作目录、标签、注解、env 在实际 Pod/容器生效；系统 UID guard、GPU/网络控制字段保留，恶意覆盖被拒绝 |
| A3 | 两轮 Stop/Start 后原 PVC UID 和文件保留；删除后系统工作盘/PV 按原规则回收，用户 PVC/PV 与数据保留 |
| A4 | 卷跨租户/专属盘、同名替换、路径冲突被拒绝；合法 Pending 不被错误排除；实际挂载冲突可归因 |
| B1 | 指定 VPC/Subnet 创建，真实子网取址及同 VPC 对照服务通信通过；错误租户/placement 被拒绝 |
| B2 | Stop/Start 和删除 Pod 触发自动重建均能重新联网；新旧 UID 交接无永久误冲突，旧地址不再被作为有效后端 |
| B3 | Prepare/Confirm/Release 丢回执、Instance/Network 重启、Consumer 快照落后、未知 CREATE 均可恢复或明确等待；覆盖快照/Confirm 乱序、closing 后迟到 open、旧 Pod Confirm 迟到，不能回退状态或提前释放 |
| B4 | 最终删除等待当前/历史 Pod、VNic/VNicIP/Nic 等依赖清理，Network released；原 GPU 退额不被网络失败错误重扣或永久扣留 |
| C1 | 旧 v1 CPU/GPU 请求重放、CPU worker 恢复、Start/Delete 和原账退额通过；新 v2 配置差异触发幂等冲突 |
| C2 | GPU 模式验证用户配置不改变冻结执行、额度占退及系统注解；真实硬件运行与仅字段传播分别报告 |
| C3 | 实际 governance/full 装配可推进网络，旧 single_pod Consumer 定向回归；真实前端页面验收独立记录 |

## 10. 方案取舍与外部前提

建议首版保留一个系统工作盘，另允许最多 8 个已有卷；用户卷默认保留；配置创建后冻结；未选网络保持现有行为；显式 VPC 使用一个 primary Subnet。这些是供审阅的产品取舍，不伪装成已经逐项批准的规则。

Notebook 原生 PodSpec 支持 PVC 与挂载路径，见 [Notebook v1.10 API](https://github.com/kubeflow/kubeflow/blob/v1.10.0/components/notebook-controller/api/v1/notebook_types.go#L23-L31)。卷同 Namespace、访问模式和延迟绑定以 [Kubernetes 存储合同](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)为基础。实际支持的镜像目录权限、CSI、PVC 删除保护、kcn/OVN 及 admission 保留键需要在真实验收环境核对；源码路径存在不等于该组合已验收。
