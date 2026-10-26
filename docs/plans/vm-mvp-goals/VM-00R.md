# VM-00R 获授权维护窗口内完成 Block 运行时前置

目标：完成VM-00R；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-00 具体变更单；用户届时确认集群无人使用并明确授权该变更、节点、重启及探针。缺一项都不得写入。

主责：受授权的集群实施 agent

唯一规则范围：S03、S04、S06；U01/U02；维护模板。

允许修改位置：仅批准的 runtime 配置输入与对应服务；批准namespace中的限定DV/PVC/探针VM；环境证据。

具体工作：

1. 读取并复核批准窗口、节点、配置来源和配置diff；备份实际配置。发现仍有不在批准中可中断的业务负载，保存事实并暂停写入，不自行关停它们。
2. 按实际containerd v1/v2或CRI-O及发行版方式只设置device_ownership_from_security_context；k3s/RKE2使用可持久化生成输入，不能盲改生成文件。
3. 按批准顺序逐节点重启对应服务，核验节点和既有基础组件；任何失败按批准恢复方案处理，不并发重启全部节点。
4. 在批准范围新建/重建受影响CDI工作负载，覆盖选定源import/clone/blank到Block和RWO；核对非root设备访问、所有候选VM节点的有效配置及一条真实VM读写。
5. 删除本次精确探针资源并核对回收，保留原错误/配置摘要/对象UID/结果。仅能力验收PASS后解除ENV-BLOCK。

产出：实际配置差异/备份位置、节点逐项结果、Block探针与清理证据、ENV-BLOCK结果。

必须验证：V01、V02。

完成条件：批准节点配置生效，选定Block路径及清理验证通过；不得据此标记ANI VM产品链路通过。

禁止扩展：当前没有后续授权时只补准备材料并报告BLOCKED_ENV；不改Filesystem兜底、不提高Pod权限、不升级组件、不改KCN/共享gate、不修改installer。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
