# VM-01C Resource 持久创建、独立 DV 与删除基础链

目标：完成VM-01C；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01A；可与VM-01B并行，现场依赖VM-00R。

主责：Resource vm

唯一规则范围：S02、S04–S06、S10–S11；C03–C04。

允许修改位置：internal/biz/vm、internal/data/vm、internal/service/vm、migrations/vm（新增）、api/vm/v1、cmd/ani-resource-service/vm.go；受信listener。

具体工作：

1. 建立租户隔离的VM/disks/operations数据与显式迁移，冻结原始创建和当前配置；沿现有模式持久受理、工作领取、pending mutation与恢复扫描。
2. 实现AcceptCreate/AcceptDelete/Get/List/GetOperation和原生adapter：确定名称→Network Prepare端口→Manual根VM→UID登记→独立DV(owner=VM)→按意图Start。
3. root/data显式Block/RWO；DV/PVC未知create用同一对象查证；WFFC不阻止消费者创建，停止态创建允许盘等待消费者。
4. 只实现首条Linux系统镜像链所需根盘、媒体来源校验、停止保盘、最终删除及cleanup refs；data_disks接口保留，未实现阶段不可默默忽略非空输入。
5. 删除可接管关闭但不丢未知mutation；保持共享源/NAD不变，网络Release经端口，异域SQL禁止。

产出：首条VM核心代码、迁移、真实PG受理恢复和原生adapter契约验证。

必须验证：V05、V07、V22首链相关用例；真实Block证据在VM-01F完成。

完成条件：隔离验证证明幂等/重启恢复/归属/UID删除成立，核心可与网络与SG拼接；现场未跑保持NOT_RUN。

禁止扩展：不使用dataVolumeTemplates，不自写CDI转换器/虚拟化controller，不写Instance表，不复制通用工作流；不先做全部CRUD再拖延纵向链。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
