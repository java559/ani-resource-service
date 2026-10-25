# VM-02B Windows 驱动、设备与必要持久状态

目标：完成VM-02B；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-02A；固定Windows版本/ISO/驱动；若所选OS要求TPM/UEFI，则其原生持久状态存储可用；快照组件/存储能力缺项只阻断后续快照恢复现场验收。

主责：Resource + 前端 + 验收owner

唯一规则范围：S05、S08–S09；C03。

允许修改位置：受管Windows物料/设备默认值、vm native映射、既有介质/console UI；必要场景验收。

具体工作：

1. 按已冻结Windows版本使用只读SATA VirtIO驱动光盘，Block根盘与匹配VirtIO网卡/磁盘驱动。
2. 浏览器完成安装、键鼠交互、识盘、NetKVM联网、Agent实际连接；去ISO后从根盘启动。
3. 若系统要求TPM/UEFI，使用KubeVirt原生持久状态，backend PVC保留Filesystem，不强套Block；停开验证必要状态。
4. 补足Windows当前功能UI表现与原生错误；后续快照/热插拔的Windows现场结果由对应卡补齐。

产出：固定版本Windows可重复安装/启停/网络/VNC证据及物料说明。

必须验证：V15中的安装/停开部分；TPM快照恢复在VM-04B补全。

完成条件：至少一套正式Windows版本按真实要求通过；材料缺失则明确NOT_RUN，不用Linux结果替代。

禁止扩展：不关闭OS要求、不自动激活、不自动注入驱动、不扩为任意Windows支持矩阵。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
