# ani-resource-service

Module: `github.com/zhangzhe-ctrl/ani-resource-service`

This repository was generated from ANI's pinned Kratos layout. It is an
independent source snapshot: builds and runtime do not require the layout.

本仓库在原 Network 服务完整历史上演进为 Resource 服务，一个 Go module、一个进程。
现有 Network 实现在 internal/{biz,data,service}/network；本批不包含 Compute/Storage。
Network 契约、数据与资源身份保持原值，演进依据见 [ADR-0006](docs/adr/0006-evolve-resource-service-preserving-network.md)。

从 [文档导航](docs/START-HERE.md) 开始阅读；其中链接当前规格、领域词汇、
设计决定、实施计划和唯一执行状态。设计目标与实际实现/验证结果分别记录。

`THIRD_PARTY_NOTICES.go-kratos-layout.txt` preserves the upstream template's
MIT notice. This generated repository intentionally has no project `LICENSE`;
its owner must make that choice before publication.

## 开发与运行

```bash
make tools
make verify
make integration
make race
make tenant-mutations
```

Image 的真实数据库、生命周期和租户隔离另用 `make image-integration`、
`make image-tenant-mutations`；需设置本次检查独占的短路径 `TMPDIR`。
完整入口和 CI 检查范围见 [运行验证](docs/runtime-verification.md)。

本次改名的生成、编译、测试及 API 驱动仅在 SSH `fedora` 执行，见 [远程约定](docs/remote-execution.md)。
正常启动需要专用 PostgreSQL 的 runtime 连接串、游标签名 secret 和 kc 集群凭据；
迁移使用独立 owner，通过显式 `-migrate` 入口执行。
具体变量、权限、启动命令和健康含义见 [运行说明](docs/runtime.md)。

`network.v1` 提供 VPC/Subnet 创建、查询、列表、删除、操作查询，以及普通容器 Attachment 协议；
由 Network 自有持久 worker 推进和恢复，共享 List/Watch、关系索引与完整审计提供观察事实。ANI 的独立 Gateway 适配及实例 owner 通过版本化 RPC 接入。
NET-05A 对新版本普通容器真实链路独立复验；VM、前端、IAM 与配额保持后续工作包边界。
当前实施结果和证据只在 [执行状态](docs/execution/status.md) 维护。

已有 Kratos 日志、中间件、trace、metrics 和优雅停机门禁继续保留。
readiness 现在包含 worker 与真实数据库状态；实际 kc adapter 是正常运行的唯一 Provider。

`make audit` 保留漏洞、Git secret、notice、许可证据和 SBOM 门禁。
生成 SBOM 需要干净的已提交源码；本轮的独立远程验证提交流程见 [运行验证](docs/runtime-verification.md)。
没有执行真实 kc 部署、数据面验收或发布，就不能据这些检查宣称网络可用。
