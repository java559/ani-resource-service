# VM-04B 原 VM 恢复、映射更新与旧盘清理

目标：完成VM-04B；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-04A、VM-03B；已停止且VMI对象不存在；原生恢复与存储前置满足。

主责：Resource + Gov + 前端

唯一规则范围：S09；C03–C04。

允许修改位置：PreviewRestore/RestoreSnapshot、vm当前配置/盘映射/cleanup上下文、UI影响预览；原生Restore adapter。

具体工作：

1. 固定FailImmediate/RandomizeNames/Vm与同根VM UID；实现五种盘集合预览，version+preview_digest绑定确认，版本变化重预览。
2. 保存snapshot disk inventory以恢复当前已卸载/已删除的快照内盘；快照外挂载新增盘成功后删除，快照外已卸载盘保留。
3. 先原生完成，再核对新PVC UID并持久映射，停止态补回数据盘hotpluggable/SCSI/serial，再精确清理已冻结旧root/data；backend由native管理，不保证失败回滚。
4. operation在cleanup确认前保持处理中；重启/超时复用进展点，不重复Restore，不覆盖回初始DV；WFFC PVC Pending不阻止结束，不隐式Start。
5. 双系统在线/离线快照都完成restore→显式Start→marker读回；恢复后在线摘挂和必要TPM/UEFI状态一并验证。

产出：有确认的原VM恢复、稳定逻辑盘映射、可恢复清理及双系统真实数据证据。

必须验证：V19–V21、V15 TPM恢复、V23恢复权限/冲突。

完成条件：五种盘集合、未知结果/cleanup恢复、恢复后热插拔及不回写旧盘均通过。

禁止扩展：不跨VM克隆、不逐盘人工CSI恢复、不自动备份补偿、不静默关开机、不在native未知时主动删旧root/data。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
