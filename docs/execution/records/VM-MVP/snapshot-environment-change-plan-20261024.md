# VM 快照及必要 backend state 的限定环境变更（本轮已被后续指示取代，不可执行）

> 2026-10-24 后续用户指示为“维持现状，不新增组件。如果报错，抛出即可。”本文件的新增五对象 SC/Profile/VSC 方案从未执行，现被该指示取代，本轮不可执行；对应拟建 JSON 和旧驱动仅作为历史准备来源，不能按旧方案 apply 或 vmstate。当前沿用 ani-block Immediate，WFFC 专用 LIVE 不适用/未覆盖，其余验收及历史结果见[Goal 最新范围](../../../plans/vm-mvp-complete-goal-20261023.md)与[唯一状态](../../status.md#vm-mvp)。后续独立原地策略已覆盖整个10～12集群后续新快照、RBD＋CephFS：仅将两既有VSC deletionPolicy设为Delete，状态 CONFIG_APPLIED_VERIFIED，见[唯一状态及实际回执](../../status.md#vm-mvp)。随后用户另行授权规范类名迁移：新 `ani-rbd-delete` / `ani-cephfs-delete`、既有Profile spec/status显式新名、旧两类正常退休，结果见唯一状态。这是用户永久集群策略，Goal清理不回退，不能运行本历史五对象方案的apply/restore/vmstate。历史Retain快照/Content及其历史类名保持，SC不变，Profile仅按新授权更新快照类引用。当前整体Goal已RESUMED，但本五对象历史方案仍不可执行；真实CSI新快照删除/restore仍NOT_RUN，不代表VM-04全面通过。

目的：在已核验的10～12集群完成一期双系统快照/原VM恢复/CSI回收，以及所选Windows若要求的持久TPM/UEFI验证。现有`ani-rbd-retain`为Retain，不能把Snapshot对象消失等同后端副本回收。KubeVirt v1.9.0的Snapshot/DeclarativeHotplugVolumes为Beta默认启用，VMPersistentState为GA；不修改feature gate。

基线：kube-system UID `5277649d-e28d-4a0a-ae76-8354365c63bc`；KubeVirt `kubevirt/kubevirt` UID `a3f905a6-9829-42d2-8f18-5ff30192ffc8`，目前未设置vmStateStorageClass。SC ani-block UID `e7813a57-3d1c-47e4-a298-86212a59e51e`；原RBD SnapshotClass UID `a1568367-0da2-4ede-86c0-f32ea790772d`。当前StorageProfile/完整参数以[只读前置](checks/virtualization-prerequisites.json)为准。执行前刷新UID/resourceVersion/规格，漂移则停止写入。

具体差异：

1. 对既有CDI StorageProfile `ani-block`，仅将spec.snapshotClass从未设置固定为它当前status已经选择的`ani-rbd-retain`，保持既有存储的Retain选择；精确保存旧spec和UID，不改变其claimPropertySets。
2. 创建本任务专属非默认SC `ani-vm-mvp-block-01a168ce`，复制ani-block的RBD driver/pool/CSI credential引用，保持Delete/Immediate，显式每次PVC模式。创建本任务非默认`ani-vm-mvp-rbd-delete-01a168ce` VolumeSnapshotClass，原RBD参数但deletionPolicy=Delete。
3. 为专属SC配置同名CDI StorageProfile，spec.snapshotClass明确选专属Delete类，claimPropertySets限定Block/RWO及Filesystem/RWO。不增加default注解，不修改其他现有SC/VSC/Profile；候选服务后续新建VM只冻结本方案明确选择的任务类，下文WFFC用例单独选择WFFC类，既有盘不改类。
4. 仅在后续必要持久TPM/UEFI路径确需时，将同一KubeVirt配置的vmStateStorageClass临时设为Immediate专属SC；WFFC不作backend类，原生backend PVC仍Filesystem/RWO，根/数据盘仍Block/RWO。该项影响配置期间所有新建backend state卷的类选择，需明确批准。没有手工重启KubeVirt/CDI/CSI/节点服务。

新增对象的可审JSON见[拟建对象](snapshot-environment-proposed-objects.json)。StorageProfile若已由CDI生成，仅对同名task SC的生成对象做带UID/RV test的spec字段更新，不接管同名异主对象。

执行顺序：刷新集群/SC/Profile及所有现有RBD StorageProfile的快照选择，备份；先pin原Profile，再建任务SC/Profile与VSC并等待CDI status收敛。若发现其他SC没有确定快照选择，本方案停止并补充具体影响，不能让新增类造成歧义。随后在任务租户/namespace以正式产品API建VM/盘/快照/恢复，原生控制器负责CSI。探针与产品都登记UID和底层handle，最多使用本任务新建VM/PVC，不调整其他负载。

验证：三节点仍Ready、原组件健康；原Profile仍选择Retain；任务Profile准确选择Delete；Block数据盘与Filesystem backend实际模式；native Snapshot/Restore必要卷完整；Snapshot删除后对应VolumeSnapshotContent及CSI副本回收。任何失败保留身份/清理引用，不强拆finalizer、不改Filesystem替代业务盘、不修改KCN。

## WFFC 必需现场路径补充（同一方案，未执行）

本轮只读刷新 `StorageClass`/`StorageProfile` List，集群 UID 不变；目标只有 `ani-block`（UID如上、RV4879、RBD、默认、Immediate）与 `ani-cephfs`（UID `686c6115-7307-4db9-8fff-5a82f772b324`、RV4875、CephFS、非默认、Immediate）。没有现成非默认 WFFC Block/RWO 类。原两 Profile UID/选择分别仍为 `83354750-8885-41e0-9c6d-5334ffdd4bcb`/ani-rbd-retain 与 `49bbc971-fc2d-4543-b7a7-02ccd13aac99`/ani-cephfs-retain；未修改任何类或 Profile。

[必需验收](../../../verification/vm-mvp-acceptance.md) V07 要求 WFFC 停止态创建完成且首次显式 Start 不死锁；V16 要求未挂 WFFC 数据盘创建完成、随后冷 Attach/Start；V20 要求原生 Restore 后新的 WFFC PVC 即使 Pending 也能结束映射与清理，保持停止态。三项均含 LIVE，现有 Immediate 产品结果和 PG/外部边界测试不能覆盖这些条件。

最小新增差异为下面一份任务 SC 与原生 CDI 同名 Profile，复用上方任务 Delete SnapshotClass，不新增第二个 SnapshotClass。SC 复制已固定 `ani-block` 的全部 RBD 参数，仅新名字、去除默认注解、binding=WaitForFirstConsumer；不把既有类改成 WFFC。Profile 只允许业务根/数据盘所需的 Block/RWO；必要 Filesystem backend 仍使用上方 Immediate 任务类，不将 WFFC 类设为 vmStateStorageClass。

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: ani-vm-mvp-block-wffc-01a168ce
  labels: {ani.vm.fixture: vm-mvp-20261023-01a168ce}
provisioner: rook-ceph.rbd.csi.ceph.com
parameters:
  clusterID: rook-ceph
  pool: ani-block-pool
  imageFeatures: layering
  imageFormat: "2"
  csi.storage.k8s.io/fstype: ext4
  csi.storage.k8s.io/controller-expand-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/controller-expand-secret-namespace: rook-ceph
  csi.storage.k8s.io/controller-publish-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/controller-publish-secret-namespace: rook-ceph
  csi.storage.k8s.io/node-stage-secret-name: rook-csi-rbd-node
  csi.storage.k8s.io/node-stage-secret-namespace: rook-ceph
  csi.storage.k8s.io/provisioner-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/provisioner-secret-namespace: rook-ceph
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
---
apiVersion: cdi.kubevirt.io/v1beta1
kind: StorageProfile
metadata:
  name: ani-vm-mvp-block-wffc-01a168ce
  labels: {ani.vm.fixture: vm-mvp-20261023-01a168ce}
spec:
  snapshotClass: ani-vm-mvp-rbd-delete-01a168ce
  claimPropertySets:
    - accessModes: [ReadWriteOnce]
      volumeMode: Block
```

在前述原 ani-block Profile 已 pin Retain、任务 Delete 类可用后，再建 WFFC SC；原生 CDI 自动生成同名 Profile 时，只对来源为本次 SC、CDI controller owner/UID 及该 Profile UID/RV 全部吻合的对象设置上述 spec。若同名外来对象存在或其它 RBD Profile 出现未确定选择则停止。新建 VM/盘通过正式 API 使用明确冻结的 WFFC 目录/类选择；现有 v3 盘与源盘不改类。不加 immediate-binding/selected-node 旁路注解，不改 CDI gate、不代用户暗中 Start、不手工绑定 PVC。

现场按 V07→V16→V20 留下真实 WFFC/Pending、operation 完成与无 VMI/消费者的证据，再仅由用户正常产品 Start 驱动绑定和 Guest marker 读回；若原生控制器未能完成 Restore 或实际未出现所需 WFFC 条件，明确记录对应失败/未覆盖，不以 `Accepted` 或 Immediate 路径替代。

清理时先正常清完两种任务类的全部 VM/DV/PVC/PV、Snapshot/Restore/VolumeSnapshot/VolumeSnapshotContent 引用及后台消费者；确认引用为0后，按记录 UID 先删除 WFFC SC、等待 CDI 退休同名 Profile（必要时按原登记 UID 删除），再清理 Immediate 任务 SC/Profile。两类均退出且任务 VSC/CSI 副本已正常清完后，删除唯一任务 Delete VSC，最后才按精确原字段恢复 ani-block Profile 的未设置状态。若可选 vmStateStorageClass 曾使用，仍先经独立动作且当前值精确匹配才恢复，再删被引用类；不强拆 finalizer 或操作后端。

本节增量已同步到同一[拟建 JSON](snapshot-environment-proposed-objects.json)与 `vm-snapshot-environment.py`：五对象分别登记，即 Immediate SC/Profile、WFFC SC/Profile 和唯一 Delete VSC。驱动固定该五对象输入 SHA，默认只读，apply 不修改 vmStateStorageClass；检查两类全部引用为0后按上述顺序清理，原 Profile 的精确单字段恢复最后执行。仅准备与纯数据检查不构成现场通过；实际 preflight/apply/restore 仍须依据本完整增量的明确批准，不另建状态台账。

恢复/清理：先正常清理任务VM、所有盘/backend、Snapshot/Restore/CSI对象并确认回收；若第4项使用且当前值仍精确为本任务设置，恢复原先未设置值；无引用后按UID删除任务SC/Profile/VSC；新增VSC消失后将原ani-block Profile spec.snapshotClass恢复原先未设置（仅字段仍等于本任务设置且UID不变时）。如果检测他人漂移或新引用，暂停对应清理并报告，不覆盖。原镜像、原SC/VSC、其他租户及获批永久runtime修正保留。

批准边界：完整Goal第4节要求共享KubeVirt/StorageClass策略变更先取得针对具体差异的批准。本文件为拟变更，不是执行或通过证据；一期其他实现与已授权的Linux生命周期继续。

依据：[v1.9 class选择优先StorageProfile](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/storage/snapshot/snapshot.go#L670)、[v1.9 gate语义](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-config/featuregate/feature-gates.go#L82)、[原生持久状态](https://kubevirt.io/user-guide/compute/persistent_tpm_and_uefi_state/)。
