# VM-05B 精确依赖、发布准备与交接

目标：完成VM-05B；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-05A；正式发布/部署按届时具体授权；准备工作可先完成。

主责：各仓owner与部署owner

唯一规则范围：S12；C01/C06；V25。

允许修改位置：正式module依赖、迁移/部署配置、版本矩阵、docs/execution/status.md与交接材料。

具体工作：

1. 先形成可审的API/module版本、服务镜像digest、迁移清单、部署顺序和回退范围；正式依赖必须可获取，删除临时验证go.work/replace。
2. 发布兼容协议后更新精确Gov/SG依赖；显式增量迁移，部署理解新合同的Resource/Gov/SG，核验写入/恢复/清理worker后再开放VM入口。
3. 写明回退先关闭新受理，仍保留理解新VM数据/命令的处理能力；不能退到会误解析VM action或丢新盘映射的旧版本。
4. 仅在具体发布/部署获得授权后执行对应写入；未获授权交付完整可审发布单并保持NOT_RUN，不阻塞文档/验证收尾。
5. 汇总实际来源SHA、正式依赖、证据、操作说明与未完成条件；更新唯一状态。

产出：精确版本/迁移/部署与回退单、前端交接、唯一状态；获授权时附实际发布结果。

必须验证：V25。

完成条件：所有可审发布准备完成；正式部署结果与准备状态分别陈述，不因任务包交付声称已发布。

禁止扩展：不自动merge/tag/release/推送部署，不降级到不理解新合同的版本，不清理历史数据作回退捷径。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
