# VM-01D 接通 VM Network consumer、NAD 和当前地址

目标：完成VM-01D；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01A、VM-01C的数据/端口；真实网络依赖VM-00R和已批准NAD准备。

主责：Resource network + vm

唯一规则范围：S07；C05；S08的VNC解耦。

允许修改位置：C05列出的biz/data/service network路径、api/network/v1、VMNetworkConsumer/NetworkPort、两个composition。

具体工作：

1. 新增vm_managed/v3严格VM→VMI→launcher链，保留v1/v2格式、摘要和owner校验；VM走VMReader，不查InstanceReader。
2. 实现Prepare.vm_expectation=12、VMPrimaryPlan=26、当前地址=27；NAD身份/config摘要与KCN Subnet独立，Validate/Prepare冻结一致。
3. 复用现有Attachment worker和快照修订，当前端点只有launcher；地址从Network正式DTO输出，过期/停止/UID冲突清空资格。
4. Stop保留Attachment，Start/Restart观察新UID；删除按根/运行对象退出→consumer closed→Release→Network独立released，不引入循环等待。
5. 缺NAD拒绝新建/Start；依赖消失不阻止按冻结UID Stop/Delete。两个装配入口都生效。

产出：新模式协议/adapter/投影/地址和现有worker接线、兼容与身份测试。

必须验证：V09、V10的隔离部分、V24旧Network路径；真实来宾部分在VM-01F。

完成条件：新旧模式严格分派、无地址跨域私读、网络关闭顺序成立；现场条件缺失不冒充通过。

禁止扩展：不新增NAD产品生命周期、固定IP、多NIC、任意GVK注册、binding插件；不改KCN或用共享默认网代替租户隔离。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
