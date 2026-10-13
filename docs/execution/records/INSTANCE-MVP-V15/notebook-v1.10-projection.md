# Notebook v1.10.0 模板投影核验

2026-10-13 对 CPU 实验出现的 `unknown field spec.template.metadata` 核对官方固定源码。**Notebook v1.10.0 的 template 只有 PodSpec，标签与注解应放在 Notebook 顶层 metadata。** ANI 的 Queue、GPU mode、plan digest 和管理标签可以通过这个官方合同进入实际 Pod，无需修改共享控制器或增加 admission。本次据此修正 RenderNotebook 与计算清理发现；实际修复/CPU 验证在[真实实验索引](live-lab.md)登记，当前进度只由 status.md 维护。

## 来源与证据范围

- 官方仓库 `kubeflow/kubeflow` 的 v1.10.0 tag object 为 `61234013ebadfe687417ebe2bd8456b9d50acace`，commit 为 `90e987bf87d3e7c900926310b00bfa16b59e41eb`；在 `ssh fedora` 中 fetch 并以 `git rev-parse 'v1.10.0^{commit}'` 确认。
- 已有[集群预检记录](cluster-preflight.json)记录 controller 镜像 `172.16.101.10:30003/ani-kf-manual-20261009/kubeflow/kubeflow/notebook-controller:v1.10.0@sha256:a0dda5d62845057c73f2984d599770ea05f5067d50583fbd388687069e843bd9`。镜像名/版本与本文来源相符，但本文没有取得构建 provenance 或做二进制重现，**digest 到源码 commit 的构建关联 not_verified**。修复后的实际 CPU 投影由独立实验继续核验。
- Fedora 来源目录为 `/home/chabking/workspace/ani-resource-service-runs/instance-mvp-v15-20261013/upstream-compat/notebook-source/`，固定原文及 SHA256 在 `snapshot/`、`kubernetes-snapshot/` 和 `source-manifest.json`。此次仅读取上游与现有 Resource 源码，未构建、未写集群。

## Schema 与创建投影

官方 `NotebookTemplateSpec` 只有 `Spec corev1.PodSpec`。CRD 的 v1、v1alpha1、v1beta1 都仅声明 `spec.template.spec`，没有 template metadata，也没有在该层启用 preserve-unknown；v1 是 storage version，三个版本均 served。因此使用 Kubernetes PodTemplateSpec 的 metadata 层级不符合 Notebook CRD。[Go 类型](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/api/v1/notebook_types.go#L26-L34)、[CRD](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/config/crd/bases/kubeflow.org_notebooks.yaml#L18-L33)。

`generateStatefulSet` 创建与 Notebook **同名、同 namespace** 的 StatefulSet。它的顶层 metadata 只设置 name/namespace，不复制 Notebook 的 ANI 标签；Pod template 初始标签为 `statefulset=<NotebookName>`、`notebook-name=<NotebookName>`，随后复制 Notebook 顶层全部 labels，因而 ANI 管理标签能够进入 Pod。Controller selector 使用 `statefulset=<NotebookName>`，ANI 不应覆盖这些控制器保留键。[创建与标签投影](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/controllers/notebook_controller.go#L361-L396)。

Controller 将 Notebook 顶层 annotations 复制到 Pod template，排除 **key 中包含 `kubectl` 或 `notebook` 的注解**，匹配区分大小写。当前这些冻结字段不会触发过滤：

| Notebook.metadata 的字段 | 实际 StatefulSet / Pod 位置 |
|---|---|
| ANI tenant、instance、owner、original CREATE 管理 labels | `StatefulSet.spec.template.metadata.labels`，继而 `Pod.metadata.labels` |
| `scheduling.volcano.sh/queue-name` | `StatefulSet.spec.template.metadata.annotations`，继而 Pod annotation |
| `volcano.sh/vgpu-mode` 及当前冻结 recipe 注解 | 同上；仍需对每个 key 核对过滤条件 |
| `accelerator.ani.io/plan-digest` | 同上 |
| `instance.ani.io/intent-version` | 新建 StatefulSet 模板可复制，但已有模板的版本更新不能依赖这个字段，见下文 |
| `kubeflow-resource-stopped` | 由控制器读取其**存在性**决定 replicas=0；启用通过删 key，value 不影响停止判断 |

来源：[注解过滤与 PodSpec copy](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/controllers/notebook_controller.go#L361-L404)。若未来冻结 GPU annotation 的 key 命中过滤规则，这份 Notebook 合同不能直接投影它；应在冻结/渲染阶段明确拒绝，不能静默接受或通过共享 admission 补写。

PodSpec 被 deep-copy，现有 schedulerName、nodeSelector、limits、workspace volume、downward API Pod UID 与安全配置仍放在 `spec.template.spec`。Controller 另为首容器补默认 workingDir/port、加入 `NB_PREFIX`，且仅在整个 Pod securityContext 为 nil 时考虑补默认 fsGroup；ANI 显式工作目录和 securityContext 可以保留。[PodSpec 调整](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/controllers/notebook_controller.go#L406-L435)。

## 已有 StatefulSet 的限制

`CopyStatefulSetFields` 会覆盖 StatefulSet 顶层 labels/annotations、replicas 与 `template.spec`，**不会更新 `template.metadata`**。因此修正一个已经创建的 Notebook 顶层注解，不能假定既有 StatefulSet/重启后的 Pod 获得新 Queue/GPU annotations。直接给 StatefulSet 顶层加 ANI labels 也会被后续 reconcile 清掉。[字段复制](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/common/reconcilehelper/util.go#L105-L133)、[reconcile 调用](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/controllers/notebook_controller.go#L138-L169)。

本次 CPU 实验应在 Render 修正后使用新建、任务专属 Notebook 验证原生投影。已创建实验对象如需重新准备，只能走该实验已有清理和保留流程。不能通过重建产品已接受的 Notebook 来更换持久 UID，也不能用修改全局控制器掩盖合同不符。冻结标签和 GPU annotations 在合法创建后应保持原值；Stop/Start 的当前业务版本仍由 Resource 持久状态和 Notebook 条件变更核验。

## 安全的所有权链与删除观测

Notebook reconcile 通过 `SetControllerReference(instance, ss, scheme)` 把 Notebook 的 UID 写为 StatefulSet controller ownerRef；控制器实际 watch 类型是 v1beta1，所以读取 ownership 时不能仅接受 ownerRef 的 `kubeflow.org/v1` 字面版本。[Notebook → StatefulSet](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/controllers/notebook_controller.go#L138-L146)、[watch 类型](https://github.com/kubeflow/kubeflow/blob/90e987bf87d3e7c900926310b00bfa16b59e41eb/components/notebook-controller/controllers/notebook_controller.go#L713-L720)。

Kubernetes v1.35.8 tag 解引用为 `1c2e10a409eb1b03f2f28f401ce935312e20d9fb`。StatefulSet 创建 Pod 时使用 `NewControllerRef(set, controllerKind)`；通用 helper 复制模板的 labels/annotations、PodSpec，并附加该 ownerRef。因此可核验真实 `Notebook UID → StatefulSet UID → Pod UID`，不能仅靠同名或标签判断归属。[StatefulSet → Pod](https://github.com/kubernetes/kubernetes/blob/1c2e10a409eb1b03f2f28f401ce935312e20d9fb/pkg/controller/statefulset/stateful_set_utils.go#L518-L523)、[模板复制与 ownerRef](https://github.com/kubernetes/kubernetes/blob/1c2e10a409eb1b03f2f28f401ce935312e20d9fb/pkg/controller/controller_utils.go#L562-L584)。

最小实现策略：

1. GET 保存 namespace 内与 Notebook 同名的 StatefulSet。必须检查其唯一 controller ownerRef 的 API group `kubeflow.org`、served Notebook version、kind、name 和**保存的 Notebook UID**。检查 Pod template 的全部 ANI 管理标签及冻结 annotations；STS 顶层无这些标签不能被当成外国对象，也不能作为发现过滤条件。
2. 继续使用 ANI 标签发现候选 Pod，并核对其 controller ownerRef 为 `apps/v1` StatefulSet、name 与上述对象一致、UID 为其真实 UID；然后验证已保存实例/plan、实际容器、workspace、GPU recipe 与 Pod UID。标签与 ownerRef 任一矛盾即拒绝。
3. 删除计算资源阶段不能用 `List StatefulSets(labelSelector=resource-id=...)` 的空结果证明没有可重建 Pod 的 StatefulSet。须检查保存 Notebook UID 所属同名 StatefulSet 与其 Pod 均已清理；API 错误、同名 UID 替换或关联不明保留未完成。必要的 UID 证据应随现有持久快照保存，以便 Notebook/STS 先后消失时恢复观测。
4. 定向回归覆盖正确 schema、顶层 metadata 投影、STS 顶层无 ANI labels、ownerRef 版本与 UID、同名替换拒绝；随后真实 CPU 实验核对返回 Notebook、STS template 与实际 Pod，完成原生启停、文件保留和存储回收。本文不代替这些行为验证。

这项修复让官方 Notebook 创建投影可表达当前冻结字段；真实 GPU limits/分配、共享/整卡及硬件释放仍需 GPU 到位后验收。
