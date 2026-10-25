# VM-03B 原生声明式在线热插拔

目标：完成VM-03B；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-03A；现场需DeclarativeHotplugVolumes有效且旧HotplugVolumes关闭；Windows现场依赖VM-02B。

主责：Resource + 前端 + 验收owner

唯一规则范围：S06；C03。

允许修改位置：vm原生卷/设备更新与观察、UI运行态盘结果；仅必要错误/回执适配。

具体工作：

1. 数据盘从首次配置具有SCSI/serial/hotpluggable；在线挂卸只更新原生声明，用既有operation观测。
2. 成功同时核对VM模板与VMI volumeStatus；显示原生等待/失败，不能模板写入即成功。
3. Linux/Windows各在真实VM至少两块盘在线挂卸与marker读写；guest先离线/卸载后确认，helper不参与Network当前端点。
4. 检查卸载保盘、重挂同盘、Stop/Start声明和数据保留；重复请求不创建新盘。

产出：在线挂卸产品能力及双系统真实证据。

必须验证：V17、V09 helper身份；恢复后热插拔另由V21验证。

完成条件：两种OS运行态验证完成且底层gate/驱动证据齐备；gate缺项保持该现场阻塞。

禁止扩展：不自建挂盘controller、不改旧热插拔API混用、不擅改共享gate、不把在线失败改成自动关机后成功。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
