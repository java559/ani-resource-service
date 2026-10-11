# Governance → Resource 调用登记

本登记覆盖现有 Network/Image 用户业务委托，统一规则见 [ANI 服务间调用规范](https://github.com/zhangzhe-ctrl/ani-governance/blob/8724a32ab7dda903e1c67def28ad206a06d09c44/docs/contracts/service-to-service-calls.md)。不新增调用者、RPC、配额类型或退额接口；Resource 当前没有向 Governance 发起的业务 RPC。

## 身份、范围和入口

- 调用服务为 `ani-governance`；客户端证书只有一个 DNS SAN `ani-governance`，无 URI、IP、email 或其他 DNS 身份。接收服务为 Resource，沿用登记的服务器字面 DNS SAN `ani-network-service`，不随仓库名称改名。
- TLS 1.3、私有 CA、双向证书验证；服务器证书禁止通配符 SAN。三种互斥装配模式共用同一业务 gRPC 的安全构造：默认/full、governance 和 vpc-read 都不提供明文 fallback。地址仅决定连接位置，不能决定权限。
- 服务器必需 `ANI_NETWORK_CLIENT_CA/TLS_CERT/TLS_KEY`；Gov 的 Network/Image 使用各自 `ANI_NETWORK_*` / `ANI_IMAGE_*` 受管客户端配置。缺证书、错误身份或配置失败直接报错。证书在构造时读取，续期后须重启重载；本次源码修改不代表共享环境已经换证书或部署。
- Gov 先完成 JWT/AK 认证、当前动作权限和套餐闸门，再将内部租户主键经持久映射转换为 Resource 租户 UUID。Resource 仅接受受信委托中的该租户，业务查询、关联与写入继续核验资源归属。
- metadata 只由可信值重建：单值 `x-ani-tenant-id`、`x-ani-actor`、`x-ani-request-id`。tenant 和 request ID 必须是规范、非零 UUID；actor 是 `governance:user:<uint32>` 或 `governance:access-key:<uint32>`，ID 必须规范、非零。报文中的 tenant、actor、direct caller 和 correlation 必须与委托一致。
- Network 的可选旧 `x-ani-operator` 只能是 actor 推导的 `gov-user-<id>` / `gov-ak-<id>`，不能覆盖 request ID。用户 Authorization、AK/SK 和外来身份 header 不转发。
- 下列完整方法清单在 handler 之前授权。`vpc-read` 进一步只允许 GetVPC；其他模式按已注册服务及清单授权。平台、Attachment、ImageRuntime、health/reflection 和所有 stream 未获 Gov 授权，默认拒绝。健康检查使用既有 admin `/readyz`。

## 完整 RPC 清单

所有条目都是用户业务委托：Network 和 Image 写入只操作当前可信租户所属资源；Image 的 GetImage/ListImages 另保留既有 `scope=platform` 只读目录，仍使用已授权租户上下文及原业务过滤。这份清单不授予平台管理、后台 owner 或其他租户访问权限。Network RPC 使用 `ANI_NETWORK_TIMEOUT`（默认 3s）；Image RPC 使用 `ANI_IMAGE_TIMEOUT`（默认 30s，最多 45s）。每次继承上游取消并设置正 deadline，部署可在原边界内调整超时。

“原键”指 API 已有的 `idempotency_key` 与原请求内容；“资源删除”沿用现有资源/删除操作状态机，不新增报文字段；查询没有业务幂等键。`x-ani-request-id` 仅用于追踪，不能替代这些业务标识。

| 完整 RPC | 幂等约束 |
|---|---|
| `/network.v1.NetworkService/GetVPC` | 查询 |
| `/network.v1.NetworkService/ListVPCs` | 查询 |
| `/network.v1.NetworkService/ListVPCCIDRPresets` | 查询 |
| `/network.v1.NetworkService/CreateVPC` | 原键 |
| `/network.v1.NetworkService/DeleteVPC` | 资源删除 |
| `/network.v1.NetworkService/GetSubnet` | 查询 |
| `/network.v1.NetworkService/ListSubnets` | 查询 |
| `/network.v1.NetworkService/CreateSubnet` | 原键 |
| `/network.v1.NetworkService/DeleteSubnet` | 资源删除 |
| `/network.v1.NetworkService/GetOperation` | 查询 |
| `/network.v1.TenantEgressService/CreateEIP` | 原键 |
| `/network.v1.TenantEgressService/GetEIP` | 查询 |
| `/network.v1.TenantEgressService/ListEIPs` | 查询 |
| `/network.v1.TenantEgressService/DeleteEIP` | 资源删除 |
| `/network.v1.TenantEgressService/BindVPCSnat` | 原键 |
| `/network.v1.TenantEgressService/GetVPCSnat` | 查询 |
| `/network.v1.TenantEgressService/GetVPCSnatBinding` | 查询 |
| `/network.v1.TenantEgressService/SetVPCSnatEnabled` | 原键及 expected_version |
| `/network.v1.TenantEgressService/DeleteVPCSnatBinding` | 资源删除 |
| `/network.v1.TenantLoadBalancerService/CreateLoadBalancer` | 原键 |
| `/network.v1.TenantLoadBalancerService/GetLoadBalancer` | 查询 |
| `/network.v1.TenantLoadBalancerService/ListLoadBalancers` | 查询 |
| `/network.v1.TenantLoadBalancerService/UpdateLoadBalancer` | 原键及 expected_version |
| `/network.v1.TenantLoadBalancerService/DeleteLoadBalancer` | 资源删除 |
| `/network.v1.TenantLoadBalancerService/GetLoadBalancerOperation` | 查询 |
| `/image.v1.TenantImageService/EnsureImageSpace` | 原键 |
| `/image.v1.TenantImageService/GetImageSpace` | 查询 |
| `/image.v1.TenantImageService/GetPublisherCredential` | 查询 |
| `/image.v1.TenantImageService/IssuePublisherCredential` | 原键 |
| `/image.v1.TenantImageService/ResetPublisherCredential` | 原键 |
| `/image.v1.TenantImageService/DisablePublisherCredential` | 原键 |
| `/image.v1.TenantImageService/RegisterImage` | 原键 |
| `/image.v1.TenantImageService/GetImage` | 查询，租户镜像或既有平台只读目录 |
| `/image.v1.TenantImageService/ListImages` | 查询，租户镜像或既有平台只读目录 |
| `/image.v1.TenantImageService/UpdateImage` | 原键 |
| `/image.v1.TenantImageService/UnregisterImage` | 原键 |

接收方允许清单实现为 [governance.go](../../internal/server/governance.go) 与 [image_identity.go](../../internal/server/image_identity.go)。修改调用时同时修改登记、允许清单和真实链路测试，不使用 RPC 前缀或通配符授权。

## 结果和重试

Gov 关闭未审查的 gRPC retry/service config；调用超时不证明操作未执行。用户重放沿用原业务键，创建报文 Attribution 和 metadata 共用本次追踪 ID。接收方的持久受理、幂等、worker 和租户约束沿用既有业务实现，不在传输层伪造成功。

客户端保留原 RPC status 与领域 details，不能根据瞬时连接状态把 DeadlineExceeded 改成其他状态。Gov 对公网继续进行既有安全错误映射，隐藏内部地址、证书和私有错误详情。异步受理不代表资源已可用或删除完成，调用方通过已登记查询观察实际结果。
