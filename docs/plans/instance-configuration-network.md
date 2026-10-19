# 容器实例配置与 VPC 接入实施计划

日期：2026-10-19。本 Goal 已明确授权本计划 IC-01～IC-05 的后端实施和 review 分支/draft PR。用户后续明确授权升级现有测试环境 Gov/Res，要求清理旧实例及其业务记录并删除旧实例配置兼容；后续真实 GPU 验收统一走现有 `tenant-a`/ACC/T4。实际进展只见[执行状态](../execution/status.md#instance-configuration-and-network-2026-10-19)。

新增字段、生命周期、数据和验收规则统一见[增量设计](../specs/instance-configuration-network.md)。本计划只分配实现顺序、文件责任和退出证据，不复制第二份合同或当前执行台账。

## 1. 交付顺序

先把 PVC、路径、metadata/env 通过正式创建入口连到真实 CPU Notebook；再接 VPC、受控 Pod 替换及真实释放。公共摘要版本和必要授权随首条主链实现。每批按新增行为做 red/green，不先完成全部字段、通用框架或边界矩阵才开始运行。

实施沿用本 Goal 的 Fedora 执行约定：本地编辑/审查/Git，生成、构建、测试、PostgreSQL、API 驱动和集群操作在 `ssh fedora` 的隔离目录执行，见[远程约定](../remote-execution.md)及[实例手册](../runbooks/instance-mvp-fedora.md)。实施前核对最新 HEAD、依赖、现有任务和真实集群身份；不把旧快照或历史 PASS 当作新候选通过。

## 2. 工作包

| 包 | 实现内容和主要文件责任 | 退出证据 |
|---|---|---|
| IC-01 最小配置纵向链 | Res Proto/business 版本、Instance Spec、Resolve/Accept、Notebook render/verify；Gov DTO/strict input、受理摘要和依赖消费；先接一个已有 PVC、一项 env/label/annotation | 正式 Gov→真实 PG→Notebook/Pod→终端，完成一次实际挂载读写和配置读取；当前 CPU/GPU 规范字节及旧配置版本拒绝回归通过 |
| IC-02 卷生命周期及目录 | Res 卷查询 Adapter、借用引用迁移、工作目录、多卷/只读及引用复核；Gov mountable-pvcs/configuration 查询、授权与错误映射 | A1—A4；两轮启停和最终删除证明用户卷保留、专属盘回收；并发/同名替换/跨租户负例及 worker 重启恢复 |
| IC-03 VPC 最小受控实例链 | Instance Network 端口和 Adapter、durable submission；Network controller_managed 模式、Consumer v2、最小 owner 链验证、当前 Pod 关系；composition 装配 | B1，加一次 Stop/Start：真实取址、对照通信、新 UID 重新联网、最终释放；旧 single_pod 定向回归 |
| IC-04 自动重建和失败恢复 | Network 多代关系、待核验候选、历史清理、地址/LB 资格；Instance 最终封闭；必要独立迁移 | B2—B4；自动重建、快照落后、迟到响应、未知 CREATE、双 worker/lease fencing 相关用例及删除清理 |
| IC-05 发布准备与前端交接 | Res/Gov 正式模块版本、OpenAPI、配置视图和前端联调说明；更新旧规格冲突段和状态证据 | C1—C3；旧环境正常清理、仅当前合同升级、新配置主链、GPU 受影响回归和可执行前端验收清单 |

IC-01 必须同时带来可运行行为和当前摘要防篡改，不作为纯合同整理批次。IC-03 也同时交付能合法换 Pod 的最小模式，不先交一个只能首次创建、无法启停的 VPC 功能。IC-04 只补有实际风险的恢复场景，不重新研究已固定接口。

IC-01/02 和 IC-03 的实现可由明确 owner 并行推进，但 shared Proto、Spec、worker 和 composition 的修改由单一集成人维护；不能让多个代理并发覆盖同一文件。前端由其 owner 按冻结合同接入，不提前展示未接通的选择器。

## 3. 文件和数据改动范围

| 所属 | 预期改动 |
|---|---|
| Res 入站与冻结 | `api/instance/v1/instance.proto`、`business.go`、`internal/service/instance/service.go`、诊断/配置响应适配 |
| Res Instance | `internal/biz/instance/types.go`、`worker.go`；`internal/data/instance/notebook.go`、`postgres.go` 及新窄卷/网络 Adapter；Instance 显式增量迁移 |
| Res Network | `api/network/v1` 中 Attachment/Consumer 版本字段；`internal/biz/network/attachment*.go`；`internal/data/network/attachment*.go`、`kc_attachment.go` 和 Provider 关系；Network 显式增量迁移 |
| Res 装配 | `cmd/ani-resource-service/instance.go`、`governance.go`、`app.go`；需要的新读取方法按现有业务 listener 逐项登记，内部网络写调用不扩大公开 allowlist |
| Gov | `api/protos/instance/service/v1/instance.proto`；`app/admin/service/internal/server/instance_*`、`service/instance_*`、`gpu_acceptance.go`、`data/instance_cpu_contract.go` 和相应 operation 版本存储；只调整 Instance 合同，不改其他 owner 的计费语义 |
| 文档 | 确认实施时修改 `docs/specs/instance-mvp.md`、`vpc-subnet.md` 的冲突段并链接新合同；Gov 前端合同/OpenAPI 同步；执行结果只进 `docs/execution/status.md` 和必要原始记录 |

附加卷和用户配置进入不可变快照，引用关系和动态网络观察分别持久化。迁移序号在实施时按最新目录分配，不预占或修改历史 migration/checksum，不自动在服务启动时迁移。新增每条租户关系须有 tenant 条件及本域复合 FK，并验证跨租户负例。

## 4. 版本发布和回退

1. 先发布可被正式消费的 Res Proto/规范化实现；Gov 用正式 module ref 和 `GOWORK=off`，不用复制生成代码或长期 replace。
2. 用当前仍可处理旧记录的产品入口正常清理旧实例，确认工作盘/网络退出和原账退额；保留恢复点及清理证据，再清除已终结的旧实例/操作/幂等记录。
3. 显式执行所需增量迁移，部署仅支持当前实例配置合同的 Res/Gov；旧配置 0/1/缺省版本明确拒绝，新写维护闸门关闭时不能回退生成旧命令。
4. 核验实际 governance/full 的 Instance、Network Adapter、Consumer 和 Attachment worker 完整装配；确认卷目录、引用保护、admission 键及实际环境前提。
5. 确认所有 Gov/Res 副本及恢复、删除、清理 worker 均支持当前合同，旧副本退出后，才开放新受理并交付前端选择器和配置表单；每次接受在原受理事务内原子保存明确版本及完整生效快照。

任务分支发布、draft PR 以及现有测试环境 Gov/Res 升级和旧实例清理均已获用户授权；合并、正式 tag/release 保持独立。回退先停止新受理，只使用经验证支持当前合同和 schema 的候选，不恢复旧实例受理或重解释账本。现有 ACC/SG/kcn/CSI/Notebook 组件及 GPU 供给组归属保持。

## 5. 验证和停止条件

先复用现有 Instance PG/Notebook adapter、Network Attachment 和 Gov 受理测试设施。默认只跑改动对应行为测试；本次删除旧实例配置分支须覆盖 0/1 拒绝、当前 CPU/GPU 恢复、原账 create/replay/delete/duplicate-refund、相关 single_pod 和 LB 地址资格。提交前按仓库要求执行 Res `make verify`、Gov `make verify-ci`；无新改动/失败/未解风险时不反复跑全量。

最终证据按同一源快照分别记录：定向测试、真实 PG/重启恢复、外部边界替身、真实 CPU/CSI/kcn 数据面、GPU 硬件、浏览器页面；使用 pass/fail/not_verified，Pending Pod、注解投影、HTTP 接受或文档检查均不替代业务验收。

遇到下列情况保留当前真实断点并收缩到必要修复：

- 已有 PVC 无可验证租户/删除保护，无法兑现引用不被替换的合同；不静默改用系统工作盘。
- 显式网络无法满足 placement、owner 链或实际数据面；不退回默认网络，不通过移除 UID 校验制造 attached。
- 未查明的 CREATE、旧关系残留或 observer 证据过期；不提前 closed/released，不手工改账或强删网络/storage finalizer。
- GPU/浏览器或外部环境缺前提；保留独立 not_verified，不覆盖已完成的真实 CPU 主链证据。

## 6. 交付边界

交付包括本批 Res/Gov 后端、可正式消费的精确模块版本、显式迁移、受影响回归、真实 CPU/CSI/网络/GPU 验收和前端合同。前端页面由其 owner 实现，浏览器状态独立记录；不能据后端接口存在推断页面已可用。退出条件不因计划文字或批次划分缩减，实际未完成项保留在唯一执行状态。
