# VM-01E 电源最小适配与可信 VNC

目标：完成VM-01E；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01B、VM-01C、VM-01D；代码/隔离测试不依赖VM-00R。

主责：Resource + Gov + SG

唯一规则范围：S08；C03、C06。

允许修改位置：vm power/native/console用例；Gov VM power/console入口；api/vm/console；SG proto、session store/fingerprint、mTLS、kubevirt adapter与现有WS路径。

具体工作：

1. 实现Start/Stop/ForceStop/Restart持久operation及当前version互斥；Stop采用原生宽限期终止语义，停止保盘/Attachment。
2. Restart提交前保存old_vmi_uid；超时先观察原请求/新旧UID，未知不盲重发；不用运行代次计数器。
3. Gov创建console会话使用稳定幂等键和可信ResolveConsoleTarget；SG新增vm_binding及专用mTLS校验，存储/fingerprint包含全部字段。
4. 连接前、原生开流后转发前核验VM/VMI身份与关闭意图；不依赖guest IP/Agent/安装完成或create操作结束。
5. 旧流随原VMI结束关闭，旧绑定失效；Resource不等待SG关闭回执，原Instance terminal路径保持。

产出：电源与console完整接线、未知结果/同名换UID/旧票据用例。

必须验证：V11–V13隔离部分、V24 SG兼容；真实键鼠与停开在VM-01F。

完成条件：接入现有运行/会话机制；只新增必要绑定和operation上下文，不引入新服务。

禁止扩展：不自建关机/运行代次/全局会话撤销系统，不让旧票据连接最新VMI，不暴露QEMU端口或让浏览器传原生目标。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
