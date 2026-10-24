# VM-01F 最小前端与 Linux Block 首条真实纵向

目标：完成VM-01F；本轮按docs/plans/vm-mvp-complete-goal-20261023.md及用户执行指令连续推进16卡，完成后按依赖继续。共享变更和正式发布仍须具体授权。

先读：docs/START-HERE.md#vm-mvp、docs/specs/vm-mvp.md、docs/contracts/vm-v1.md、docs/plans/vm-mvp.md中本卡、docs/verification/vm-mvp-acceptance.md；进入实际仓库后读AGENTS、START-HERE/CONTEXT及适用remote-execution；本轮重任务仅ssh fedora、集群仅172.16.101.10～12，Git传输使用SSH。若已将正式文件接入仓库，用其唯一最新适用版本，不另抄第二份规则。

前置：VM-01B–VM-01E代码通过；目标Block能力已验证（需变更时VM-00R PASS）；明确本卡环境/对象写入授权；已定位现有前端与租户NAD/源。

主责：前端 + Gov/Resource/SG 联调

唯一规则范围：S01、S04、S06–S08、S10；C03。

允许修改位置：已固定的现有前端VM菜单/页面/API client/noVNC接入；上述服务必要最小修复；受控验收记录。

具体工作：

1. 接现有UI风格/认证/API client，完成VM列表/创建/详情/操作状态/console/电源/删除；筛选无GPU规格，输入GB，不展示任意硬件/YAML。
2. 通过真实Gov→真实PG→Resource产品入口创建Linux qcow/raw的Block根盘VM；验证真实浏览器画面、键鼠登录。
3. 在真实租户子网验证DHCP、DNS、出站、两节点guest唯一marker HTTP与跨租户拒绝；停止/再启动盘marker保留、Attachment不变、IP可变化。
4. 覆盖同键重放、受理重启、Restart丢回执、旧票据拒绝；缺guest IP时控制台仍能用于排障。
5. 通过产品删除核对VM/盘/网络及旧binding；记录API受理、原生状态、guest/browser证据，不用202/Ready代替。

产出：首条真实纵向产品用例、最小可用UI与精确清理证据。

必须验证：V02结果引用；V08、V10–V13、V22首链、V23关键拒绝。

完成条件：Linux创建→浏览器→真实租户网→启停→删除闭环全部通过；此后再扩ISO/Windows/多盘/快照。

禁止扩展：不新建前端项目，不把未授权环境变更夹带进联调，不用Filesystem、共享默认网、Pod ping或WebSocket握手替代验收。

执行约束：保留无关未提交修改；真实环境先核身份和获准对象范围。device_ownership_from_security_context的共享runtime变更仅VM-00R在用户后来确认空闲并明确授权后可执行，任何其他卡不得顺手修改。前置不满足时完成可独立进行的代码/隔离验证，记录准确阻塞，不换Filesystem或共享网络制造通过。

报告：按docs/plans/vm-mvp-templates/task-report.md提供变更文件、实际源码/host/命令/退出码、验收ID的PASS/FAIL/NOT_RUN、清理结果与下一张可执行卡；更新既有唯一状态，按完整Goal依赖连续推进下一张可执行卡。未跑现场要明确NOT_RUN，不能将本卡代码完成等同一期产品验收。
