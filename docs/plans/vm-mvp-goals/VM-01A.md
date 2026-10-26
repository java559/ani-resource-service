# VM-01A 固定 VM 协议、权限与 compute 解析

目标：完成VM-01A；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-00；不依赖VM-00R。

主责：Resource + Gov API owner

唯一规则范围：S02、S04；C01–C04、C06。

允许修改位置：新增Resource api/vm/v1、api/vm/console、internal/service/vm、internal/biz/vm必要类型；compute usage与scope；Gov Module/PlanModule/Api/permcode及公开DTO。

具体工作：

1. 按C01/C03/C06生成正式业务Proto和纯console子模块；固定FullMethodName常量、字段/tag、请求/响应/error reason。只生成本期合同，不实现空provider框架。
2. 接入VIRTUAL_MACHINE usage与功能module=17，权限和精确方法scope；Resource ResolveCreateSpec内部一次调用compute，组合媒体/磁盘/网络只读校验。
3. 实现VM v1 canonical与创建摘要：vm_contract_version唯一、冻结原生来源/namespace UID、十进制GB/CPU拓扑/内存映射；拒绝公开输入可信原生身份。
4. 冻结网络期望和console binding字段；保持已有Instance/compute API字节及纯console依赖边界。

产出：可生成编译的协议、字段/错误/权限映射、Resolver最小实现与规则检查。

必须验证：V03、V06；租户/scope错误用例。

完成条件：协议/生成/影响范围检查通过，Resolver仅只读且冻结结果稳定；console module不依赖Resource运行时。

禁止扩展：不新增CPU/mem/disk配额、preset版本、VM专属GPU禁令、全量API拆包；不以临时本地replace宣称正式发布。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
