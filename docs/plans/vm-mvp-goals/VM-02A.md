# VM-02A 预置介质目录与 Linux ISO 安装

目标：完成VM-02A；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01F；已批准且固定的Linux ISO/驱动物料；代码可在现场依赖满足前准备。

主责：Resource + Gov + 前端

唯一规则范围：S05–S06；C03。

允许修改位置：vm media catalog/native adapter、ListMedia/SetMedia/EjectMedia/SetBootDevice、Gov/UI介质与启动设置。

具体工作：

1. 完成只读受管目录、来源UID/内容校验、租户专属ISO副本；SetMedia首次受理冻结本次兼容来源及副本身份，重放不重解析，执行前重核，不回写初始create_spec。共享源不可写且不归用户VM。
2. 空白Block系统盘、预定义安装/驱动光驱与有限启动顺序；通过VNC手动安装Linux。
3. 介质更新区分持久意图与运行态实际结果；仅模板生效时配置operation可结束，返回pending_restart，不占住互斥等Start、不静默重启；替换/弹出副本仍保留到最终删除。
4. 去除安装介质、切系统盘优先，重启后从原系统盘登录并读marker；介质副本纳入最终清理。

产出：只读介质入口、Linux ISO安装/摘盘/磁盘启动产品链。

必须验证：V14、V07来源隔离、V22媒体清理。

完成条件：Linux ISO与系统镜像两条链各有独立证据，不假造Installed状态。

禁止扩展：不做镜像管理、自动安装、qemu-img转换、自动驱动注入；不扩大CDI跨namespace授权。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
