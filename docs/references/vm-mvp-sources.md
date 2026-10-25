# VM v2任务包核对来源与基线

本文件保留不可变任务包的制包来源，所述“本轮”“用户确认”均指制包时点；执行授权、目标集群和位置以[完整Goal](../plans/vm-mvp-complete-goal-20261023.md)及当前用户指令为准。当前状态见[唯一状态](../execution/status.md#vm-mvp)。

核对日期：2026-10-23（Asia/Shanghai）。以下为只读源码和官方文档核对；未访问或修改真实集群，未构建、运行服务或执行产品验收。

## 用户材料与本轮决定

输入为两份附件：业务规格vm-mvp.md、纵向计划vm-mvp(1).md，二者配套，不把后者当成前者的新版本。用户随后确认：

- 容器与VM共用action；本期不接CPU/mem/disk配额；电源修正但保持简单。
- 设备所有权runtime选项确实未配置，可列为前置；待集群无人使用后，由用户另行授权agent修改。
- 要求输出细化且复核无前后矛盾的任务包。本次不授予集群或代码实施权限。

## 代码基线

| 项目 | 本轮固定ref | 核对范围与边界 |
|---|---|---|
| Resource | 63e138174831ba7cf32452e6216143a147dadc82 | main未变化；compute/Instance/Network、API模块与装配 |
| Governance | a1dcdd5f14ee607e25131ce8816ff8d0a85faa76 | 最新main；较前轮0fd2a4a仅增加inference文档等4文件，本文所核受理/action/配额代码未变 |
| Session Gateway | fb354938978250506cb22409e6ce8545d779ab4f | main未变化；VNC路径、Target、会话存储/校验与Instance窄协议 |
| KubeVirt | v1.9.0 | 原生VM/DV、gate、power、Snapshot/Restore、hotplug |
| CDI | v1.66.1 | Block、设备所有权、DataVolume、WFFC与clone |
| KCN现场/本地源码 | 仅沿附件固定记录 | 本轮无法独立认证现场镜像/运行状态或该私有源修复情况；不宣称secondary问题已修 |
| 前端 | 待VM-00固定实际仓库/ref/路径 | 当前检索未可靠定位，不猜项目或目录 |

基线用于审查，不要求覆盖执行时已有更新。实施前核受影响差异，冻结实际候选。GitHub链接是证据，不是构建依赖或写入授权。

## R01 Resource 分层、模块与执行约定

- [AGENTS](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/AGENTS.md)
- [单进程领域演进ADR](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/docs/adr/0006-evolve-resource-service-preserving-network.md)
- [已有Instance业务协议](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/api/instance/v1/instance.proto)
- [已有terminal独立小module](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/api/instance/terminal/go.mod)
- [compute解析](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/biz/compute/resolution.go)
- [远程执行约定](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/docs/remote-execution.md)

## R02 Gov action 与空账项

- [合同的owner特化与CPU空charges](https://github.com/zhangzhe-ctrl/ani-governance/blob/a1dcdd5f14ee607e25131ce8816ff8d0a85faa76/app/admin/service/internal/commandcontract/contract.go)
- [一owner一adapter及owner/action查找](https://github.com/zhangzhe-ctrl/ani-governance/blob/a1dcdd5f14ee607e25131ce8816ff8d0a85faa76/app/admin/service/internal/service/quota_adapter.go)
- [事务先写operation、按items写charge及空charge退款拒绝](https://github.com/zhangzhe-ctrl/ani-governance/blob/a1dcdd5f14ee607e25131ce8816ff8d0a85faa76/app/admin/service/internal/data/quota_ledger_repo.go)
- [现有Instance电源/console入口](https://github.com/zhangzhe-ctrl/ani-governance/blob/a1dcdd5f14ee607e25131ce8816ff8d0a85faa76/app/admin/service/internal/service/instance_service.go)
- [现有Module枚举](https://github.com/zhangzhe-ctrl/ani-governance/blob/a1dcdd5f14ee607e25131ce8816ff8d0a85faa76/api/protos/identity/service/v1/module.proto)
- [最新仅文档更新提交](https://github.com/zhangzhe-ctrl/ani-governance/commit/a1dcdd5f14ee607e25131ce8816ff8d0a85faa76)

本包的VM action/codec/module接线是新增设计，不声称以上代码已经包含VM能力。

## R03 Network 现有约束

- [Attachment协议与字段号](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/api/network/v1/network.proto)
- [Prepare与现有Subnet引用](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/data/network/attachment.go)
- [v2固定Notebook链](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/biz/network/attachment_managed.go)
- [现有Instance consumer](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/data/instance/network.go)
- [原生关系、Subnet注解与地址证明](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/data/network/kc_attachment.go)
- [managed owner核验](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/data/network/kc_attachment_managed.go)

vm_managed/v3、VMPrimaryPlan及新的正式地址DTO均为本包拟新增合同。

## R04 Session Gateway

- [当前session协议](https://github.com/zhangzhe-ctrl/ani-session-gateway/blob/fb354938978250506cb22409e6ce8545d779ab4f/api/proto/session/v1/session.proto)
- [原生VNC adapter](https://github.com/zhangzhe-ctrl/ani-session-gateway/blob/fb354938978250506cb22409e6ce8545d779ab4f/internal/runtime/kubevirt/adapter.go)
- [会话管理](https://github.com/zhangzhe-ctrl/ani-session-gateway/blob/fb354938978250506cb22409e6ce8545d779ab4f/internal/session/manager.go)
- [流结束与WebSocket清理](https://github.com/zhangzhe-ctrl/ani-session-gateway/blob/fb354938978250506cb22409e6ce8545d779ab4f/internal/transport/websocket/connectedsession/module.go)
- [Resource现有SG专用校验入口](https://github.com/zhangzhe-ctrl/ani-resource-service/blob/63e138174831ba7cf32452e6216143a147dadc82/internal/server/instance_terminal.go)

现有VNC流不等同于已支持本包VM UID绑定；不声称现有SG有按VM全量撤销/关闭确认接口。

## U01 Block 设备权限

- [CDI设备所有权配置与同类Permission denied](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/doc/block_cri_ownership_config.md)
- [Kubernetes非root容器与设备说明](https://kubernetes.io/blog/2021/11/09/non-root-containers-and-devices/)

配置缺失来自用户确认；实际原因与修复效果须维护窗口验证。

## U02 CDI 盘、来源与克隆

- [DataVolume含Block模式](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/doc/datavolumes.md)
- [clone planner模式处理](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/pkg/controller/clone/planner.go#L489)
- [PVC clone容量/准备检查](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/pkg/controller/datavolume/pvc-clone-controller.go#L465)

## U03 原生资源与盘后端

- [CPU/内存request与limit](https://kubevirt.io/user-guide/compute/resources_requests_and_limits/)
- [v1.9默认拓扑实现](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/defaults/defaults.go#L261)
- [Filesystem disk.img与Block](https://kubevirt.io/user-guide/storage/disks_and_volumes/)

## U04 WFFC 与独立 DV

- [CDI WFFC说明](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/doc/waitforfirstconsumer-storage-handling.md)
- [VM原生消费者/盘处理](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go#L512)
- [DV模板引用校验](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/admitters/data-volume-template.go#L76)
- [原生owner管理](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/controller/controller_ref_manager.go#L338)

本包选择独立DV是为固定卸载保盘和清理行为，不是说上游不支持DVT。

## U05 热插拔与 gate

- [v1.9 active gates](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-config/featuregate/active.go)
- [v1.9 inactive gates](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-config/featuregate/inactive.go)
- [旧HotplugVolumes对声明式路径的影响](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/hotplug/hotplug.go#L97)
- [VM卷热插拔控制](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go#L3568)

目标v1.9中VMPersistentState已GA，不应照滚动文档追加旧实验gate；Snapshot/DeclarativeHotplugVolumes以实际目标配置核验。

## U06 电源

- [Stop宽限期后终止](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-handler/vm.go#L1715)
- [域终止实现](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-launcher/virtwrap/manager.go#L2178)
- [Restart读当前VMI并发Stop/Start请求](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-api/rest/lifecycle.go#L395)
- [Stop API](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-api/rest/lifecycle.go#L604)

## U07 Windows 与持久状态

- [VirtIO驱动](https://kubevirt.io/user-guide/user_workloads/windows_virtio_drivers/)
- [TPM/UEFI backend storage](https://kubevirt.io/user-guide/compute/persistent_tpm_and_uefi_state/)

## U08 网络

- [Multus主网络](https://kubevirt.io/user-guide/network/interfaces_and_networks/#multus-as-primary-network-provider)
- [launcher注解/owner传播](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/services/template.go)
- KCN私有源码/真实现场本轮无法独立访问认证，原附件探测只作前置线索，正式验收重走真实租户网络。

## U09 快照恢复

- [原生Snapshot/Restore指南](https://kubevirt.io/user-guide/storage/snapshot_restore_api/)
- [Restore目标/PVC映射](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/restore.go#L659)
- [Pending WFFC PVC可继续恢复](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/restore.go#L578)
- [DVT差集清理与独立DV→PVC](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/restore.go#L925)
- [旧backend清理先于最终Complete写回](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/restore.go#L296)
- [旧backend PVC删除](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/restore.go#L1324)

## U10 回收策略

- [Kubernetes PV回收](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#reclaiming)
- [VolumeSnapshot生命周期](https://kubernetes.io/docs/concepts/storage/volume-snapshots/)

Delete策略与实际回收都须验证；文档选择不等于当前环境已配置。
