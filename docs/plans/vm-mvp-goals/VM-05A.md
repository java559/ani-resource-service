# VM-05A 全链清理、兼容与剩余风险验收

目标：完成VM-05A；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01F、VM-02B、VM-03B、VM-04B；沿用同一固定候选。

主责：跨服务联调owner

唯一规则范围：全部规格；验收矩阵V01–V24。

允许修改位置：受影响服务/前端的必要修复与docs/execution/records/VM-MVP；不新建测试平台。

具体工作：

1. 用同一候选补齐未执行格子，已通过且未变更的证据可复用，不无理由全量重跑。
2. 覆盖导入中、停止态、在线热插拔后、Restore映射/cleanup中删除；目标未知先查证，所有当前/卸载/旧引用资源完整回收且共享源/NAD不变。
3. 定向回归compute原用途、容器CPU/GPU action与账项、Inference受影响分派、Network v1/v2、SG Instance terminal。
4. 核验跨租户、同名换UID、当前授权重放、观察过期、预览冲突、旧票据与cleanup中进程重启；没有CPU/mem/disk账户变化。
5. 分开报告代码/真实PG、边界替身、真实集群、guest/browser；未跑保持NOT_RUN，失败不降范围。

产出：完整V01–V24覆盖索引、残留扫描、兼容结果、必要最小修复。

必须验证：V22–V24及此前尚未闭合项。

完成条件：全部一期必需验收已通过或明确保持阻塞；有必需NOT_RUN/FAIL不得标一期完成。

禁止扩展：不强删finalizer、不清库清namespace制造清理成功、不修无关项目、不增加库存/配额/工作流系统。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
