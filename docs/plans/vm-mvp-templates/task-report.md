# VM任务执行报告模板

## 1 身份与范围

- Task ID：
- 当前用户授权/Goal引用：
- 实际执行host与适用执行约定：
- Resource/Gov/SG/前端源码SHA、dirty清单摘要：
- 当前阶段：准备 / 代码与隔离验证 / 真实现场 / 发布准备 / 获授权发布：
- 实际改变的文件、schema、配置或原生对象：

## 2 完成结果

说明实现的具体行为、调用入口及仍缺的部分。不要仅罗列文件，也不要把ACK/Ready/握手视为整条业务完成。

| 验收ID | CODE | PG | BOUNDARY | LIVE | GUEST | BROWSER | 证据与原因 |
|---|---|---|---|---|---|---|---|
| 待填写 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 待填写 |

每层只用PASS/FAIL/NOT_RUN；不适用层说明理由，不用另一层覆盖。附实际命令、退出码、版本/digest、原生UID，去除凭据/票据。

## 3 资源与清理

- 本任务创建/变更对象及UID：
- 盘/快照/网络/会话的清理或保留结果：
- 仍在cleanup refs中的资源及原因：
- 共享源/NAD/其他租户是否保持：
- 若结果未知，沿原operation/对象继续查证的步骤：

## 4 依赖与下一步

- 本卡：CODE_PASS/LIVE_NOT_RUN 或实际相应结果：
- 阻塞：事实、受影响能力、可继续的独立工作：
- 需要用户后续授权的具体变更单（若有）：
- 下一张可执行任务：
- 唯一状态更新链接：

只更新Resource docs/execution/status.md#vm-mvp及其证据索引；本轮按完整Goal依赖继续下一张可执行卡。文档PASS不能替代产品验收。
