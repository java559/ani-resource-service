# VM-03A 多数据盘、冷挂卸与精确删除

目标：完成VM-03A；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01F；相关API已在VM-01A冻结。

主责：Resource + Gov + 前端

唯一规则范围：S06、S10–S11；C03–C04。

允许修改位置：vm disks/operation/repository、CreateDisk/AttachDisk/DetachDisk/DeleteDisk、Gov/UI磁盘列表和确认。

具体工作：

1. 完成创建时data_disks数组与后续blank自有盘；每盘稳定disk_id/volume_name/serial，Block/RWO/整数GB。
2. CreateDisk声明/身份登记后结束，允许WFFC；冷挂卸以持久配置/逻辑关系完成，不能等不存在VMI或盘Succeeded占住Start。
3. 至少两块数据盘各写marker，卸载保留、再挂原盘，关开机数据保留；拒绝跨VM/跨租户引用。
4. 单盘删除要求原生已卸载，带UID清理DV/PVC；保留必要tombstone与cleanup refs，为快照还原旧逻辑盘提供身份。

产出：完整多盘冷态管理与删除UI/API、PG/原生语义和guest验证。

必须验证：V16、V22多盘清理、V23磁盘身份。

完成条件：两块数据盘冷挂卸/再挂与精确删除通过，盘准备状态不冒充可用。

禁止扩展：不格式化/分区/扩容guest，不借用任意已有PVC，不做独立云盘、共享盘或单Pod排他检查。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
