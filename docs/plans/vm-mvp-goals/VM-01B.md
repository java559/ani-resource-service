# VM-01B 容器与 VM 统一 action 受理和投递

目标：完成VM-01B；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01A；不依赖VM-00R。

主责：Gov

唯一规则范围：S02；C02–C03。

允许修改位置：Gov internal/commandcontract、service/quota_adapter.go、command_contract.go、gpu_acceptance.go、managed_admission.go、instance_cpu_contract.go、quota_ledger_repo.go调用点、Resource client/composition、Gov VM基础BFF service/路由。

具体工作：

1. 保留一owner一adapter registry，建立唯一静态action定义；容器create/delete与VM create/delete同路查表，去除共用层owner→Instance的usage/version/hash/类型断言。
2. VM canonical走自己的codec；历史InstanceContractVersion列按C02占位兼容，原Instance CPU/GPU与Inference合同不改字节、不放开旧禁用版本。
3. 沿既有Gov事务/租约/worker保存VM create/delete，空items/charges不写扣退额；空charge合法性按各自已注册合同判断。
4. 实现当前授权后幂等、严格durable ACK、尚未送到Res时的operation查询；未知投递结果复用原ID/命令。
5. 完成C03的VM列表、详情、创建、删除及operation查询BFF；列表/详情经当前授权调用Resource，不进配额投递。既有compute目录透传VIRTUAL_MACHINE用途和对应读取权限。
6. 按C03.3复用canOperate实现Gov删除持久受理栅栏；暂停DELETE投递时拒绝新直达变更/会话，保留原查询与合法重放；处理在途请求及expected_version冲突，不覆盖旧命令。

产出：统一action路由、VM持久投递接线、真实隔离PG和兼容用例结果。

必须验证：V04、V05（含C03.3删除栅栏）、V24中受影响受理/配额合同。

完成条件：CPU容器、GPU容器、VM同一选择规则；VM无资源账户变化，旧合同重放不退化。

禁止扩展：不注册第二个Resource adapter，不创建通用动作引擎，不把电源/盘/快照塞入Gov配额ledger，不重命名全部旧GPU表/类。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
