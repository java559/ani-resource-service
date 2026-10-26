# v2 一致性复核记录

历史制包证据：正文中的事实、PASS及未执行声明仅属于原任务包生成时点；不随实施重写。当前执行结果仅见[唯一状态](../../status.md#vm-mvp)，本轮授权见[完整Goal](../../../plans/vm-mvp-complete-goal-20261023.md)。
日期：2026-10-23。复核对象：业务规格、正式合同、16张任务卡/Goal、验收矩阵与环境模板。本记录是设计复核，不是代码实施或现场测试报告。

## 1 决定与旧版冲突处理

| 主题 | v2统一口径 | 规则落点 |
|---|---|---|
| Filesystem与Block | root/data显式Block；ISO与TPM/backend按原生模式；无静默降级 | S03–S06 |
| runtime前置 | 用户确认缺项；未来空闲+明确授权后VM-00R才写入。代码工作不被一起卡住 | S03、VM-00/00R |
| 容器与VM | 一个Resource owner/adapter，正式action决定合同/usage/handler | C02、VM-01B |
| 配额 | 本期无CPU/mem/disk扣退额；保留Gov投递及原CPU容器空charge/GPU合同 | S02、C02 |
| 单位 | 正整数十进制GB、步长1GB，原生NG；内存仍MiB | S04、C03 |
| 电源 | 原生Manual/宽限期终止；Restart现有operation记录old_vmi_uid，未知不盲重试 | S08、VM-01E |
| 磁盘声明 | 固定独立DV；卸载保盘，业务独占允许原生辅助Pod | S06 |
| 规格冻结 | 初始审计输入与current configuration分开；Start/重放不重解析目录 | S04、S11 |
| 网络 | v3只新增VM；NAD与Subnet分开；旧v1/v2不迁移；Stop保留Attachment | S07、C05 |
| VNC | 可信VM/VMI绑定；不依赖GuestIP/Agent/Installed或Create全完成 | S08、C06 |
| Restore | 同根VM、五种盘集合、native完成后映射/旧root-data清理；不回写旧DV | S09 |
| 范围 | Linux/Windows、多盘、热插拔、在线/离线快照保留；缺项不自动后置 | S01、矩阵 |

## 2 交叉审阅实际修正的问题

1. **空charges限定误伤既有CPU容器。** 改成仅新增VM支持，原Instance CPU/GPU合同仍保持。
2. **Resource等待不存在的SG关闭回执。** 完成依据原生目标退出与binding失效；SG流关闭由真实端到端验收证明，不新增回执服务。
3. **console公开输入缺稳定幂等键。** 新增Gov CreateConsoleSessionRequest，key映射SG、version映射Resolve，签发不新增VM operation/version。
4. **幂等重放中expected_version歧义。** 原请求可在DB版本变化后重放；修改请求内expected_version属于异意图冲突。
5. **Network closed与released循环等待。** 固定原生VM/VMI/launcher退出→consumer closed→Release→Network独立released。
6. **Resolve冻结NAD但Prepare缺期望入参。** 增加vm_expectation=12，旧模式不改变canonical/指纹。
7. **NAD依赖失效阻塞清理。** 新建/启动拒绝漂移，Stop/Delete仍按已登记UID清理。
8. **冷挂卸错误等待VMI。** 冷态完成看持久配置；在线才核VMI volumeStatus。
9. **CreateDisk/WFFC占住后续Attach/Start。** DV声明和身份登记即可完成CreateDisk，不等待消费者才能产生的Succeeded。
10. **停止态Restore错误等待WFFC Bound。** 允许Pending，原生完成/映射/cleanup后结束，再由用户显式Start。
11. **“所有已卸载盘不受恢复影响”错误。** 快照内已卸/已删仍恢复；只有快照外已卸盘保留不变，快照inventory保存逻辑ID。
12. **独立DV旧盘被误认为原生自动清理。** 不把deletedDataVolumes当完整证明，成功后精确清理已记录旧root/data。
13. **DV→PVC恢复丢失hotpluggable。** 停止态补齐冻结设备合同，新增恢复后在线摘挂验收。
14. **Restore失败时保留所有旧卷承诺过宽。** 仅限制平台主动删除旧root/data；backend原生清理不承诺失败回滚/全保留。
15. **Windows安装被快照环境过度阻塞。** 安装/停开只要求必要持久状态，快照能力缺项只阻断后续快照恢复。
16. **VM-00允许改AGENTS/执行规则的歧义。** 这些文件只读遵循，从允许修改范围移出。
17. **目录位置与独立API module事实。** VM业务包沿Resource根module；只有SG所需console采用已有terminal模式的小module，不全量拆包。
18. **新协议编号可靠性。** 已核Module17、Network Attachment26/27与Prepare12、SG Target5在固定基线可用；后续冲突不得静默重号。
19. **ForceStop确认没有请求落点。** 明确destructive_confirmation与原生GracePeriod=0；运行中Detach才要求guest准备确认。
20. **SetMedia新来源冻结及配置操作悬挂。** 每次新请求单独冻结来源/副本，重放不重解析；仅模板更新可结束并显示pending_restart，不占住后续Start。
21. **快照partial一律拒绝过严。** 统一为必要root/data/所需backend完整且原生可用；可选介质的excluded如实展示。
22. **基础Gov查询入口漏分工。** List/Get/Create/Delete/operation查询BFF明确归VM-01B，后续功能入口归各卡。
23. **协议验收形成后续卡等待。** V03分为01A编号冻结与VM协议生成、01D Network、01E SG，不要求01A等待后续实现。
24. **不扣额与不查余额的验收混淆。** V05新增无资源账户仍可受理；V13单列console权限/短时幂等及不增VM操作/版本。
25. **正式文档落点与引用歧义。** README给出逐文件映射；VM-00同步改写链接/文字路径，空白模板与实际记录分开。

三个审阅方向分别核对Gov/SG、Network/KCN接口边界、上游存储/恢复/电源，并交叉检查任务依赖。上述问题已在主规格/合同/计划及Goal中统一，不靠在某一处追加例外掩盖冲突。

## 3 当前可确认与不可确认

已确认：固定源码接口/主要语义、本包单一规则分工、任务依赖没有上述循环、字段与action对齐、GB单位、授权边界、各Goal长度限制及内部文件引用。

尚未验证：runtime实时配置及修复效果、KCN现场镜像、目标CSI能力、Linux/Windows实际物料、前端实际仓库路径、实现代码、构建/PG测试和任何真实产品链路。本轮没有执行这些操作。它们按VM-00及后续任务记录，不伪填通过。

本复核解决已发现的实质矛盾；它不构成对未来实现零缺陷的保证，也不允许agent根据自己的偏好改选业务范围。遇到实际冲突须记录具体证据和受影响卡，先推进其他已授权工作。

## 4 文件级校验

交付前执行：Markdown内部链接目标检查、16张Goal字符数检查（每张<4000）、任务ID/验收ID映射检查、模板YAML解析、压缩包目录/内容哈希核验。结果附在同目录的[package-validation-20261023.md](package-validation-20261023.md)。该校验只证明任务包结构可用，不等价于V01–V25产品验收PASS。

