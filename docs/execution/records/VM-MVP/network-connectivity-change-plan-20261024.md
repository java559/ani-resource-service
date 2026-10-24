# VM 任务基础内网准备差异（待批准，未执行）

目的：让本任务两个 VPC 的真实 VM 到达集群 DNS `10.96.0.3` 与 Fedora 唯一正文 HTTP `172.16.101.31:39190`。这是 VM 验收的外部网络前置，不计为 Network 产品 API 验收，也不证明公网 Internet 出站。现有真实失败仍见 [v2 网络结果](checks/product-linux-v2-network.json) 和 [只读诊断](checks/product-v2-network-egress.json)。

2026-10-24 范围修订：第二真实租户及 VM 已按正常产品接口创建，以下五对象提案替代此前仅覆盖第一租户的三对象提案。旧提案批准不能自动扩至第二租户；此修订仍待明确批准、未执行。

只读结论：现行 `kcn-system/kcn-config` UID `b4be2ccc-1f81-466b-8a66-84f7a2c99115`、resourceVersion `54299998` 的 `intranetNetworks` 已包含 `172.16.101.0/24`、`10.96.0.0/16`、`10.16.0.0/16`；实际 ServiceCIDR `kubernetes` UID `2b0fda89-ea60-4588-b989-e4d40e36d7ce` 为 `10.96.0.0/16`。目标范围无需改 KCN 配置。共享 default VPC `kcn-system/kcn-cluster` UID `d0235860-f51a-4e63-8f4f-1ca1b34bc04f` 当前 Ready；候选 DB 的平台池、base connectivity 与 task EIP/SNAT 为空，不代表 KCN 故障。

正常 API 已有 Gov 平台 Intranet pool create/verify/allocate/default/delete 等七个入口，但当前平台用户尚未登记精确池权限；新池的 allocation 还要求当前拓扑/镜像与真实双向流量验证，不能用 Ready 或历史结果代替。没有公开 `EnsureVPCBaseConnectivity`/rollout 入口；现有受审阅 plan SHA 约束的 admin backfill CLI 可在池验证后仅处理候选 DB 的 task VPC。不能把现有 Public pool/provider UID 插入候选数据库或自动接管。本方案因此只申请一个任务 Intranet Subnet 及每租户一对 EIP/Snat，共五个任务专属原生对象，作为受管业务开通之前的外部验收前置。

执行位置仅 Fedora，集群仅 `172.16.101.10–12`；在全局重锁下顺序创建以下对象。所有对象记录新 UID，使用 `vm-mvp-test.ani.io/run=01a168ce` 标签；不冒充 Network 受管资源标签、不写业务数据库。以下参数在批准后执行前仍须通过完整 CIDR 冲突校验和 names-absent 检查；`10.232.254.240/29` 是明确候选值，校验失败则停止，不自行选替代网段。

```yaml
apiVersion: networking.kubercloud.com/v1
kind: Subnet
metadata:
  namespace: kcn-system
  name: vm-mvp-01a168ce-intranet
  labels: {vm-mvp-test.ani.io/run: 01a168ce}
spec:
  type: Intranet
  ipVersion: IPv4
  cidrBlock: 10.232.254.240/29
  gatewayIP: 10.232.254.241
  gateway: kcn-system/kcn-cluster
  excludeIPs: [10.232.254.241]
  allowedNamespaces:
    from: Selector
    selector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: In
          values:
            - tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c
            - tenant-d87c4dbd-04fa-4066-9ef6-c6fa4217f316
  enableDHCP: false
  natOutgoing: false
---
apiVersion: networking.kubercloud.com/v1
kind: EIP
metadata:
  namespace: tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c
  name: vm-mvp-01a168ce-intranet-eip
  labels: {vm-mvp-test.ani.io/run: 01a168ce}
spec:
  subnet: kcn-system/vm-mvp-01a168ce-intranet
  ipVersion: IPv4
---
apiVersion: networking.kubercloud.com/v1
kind: Snat
metadata:
  namespace: tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c
  name: vm-mvp-01a168ce-intranet-snat
  labels: {vm-mvp-test.ani.io/run: 01a168ce}
spec:
  eip: vm-mvp-01a168ce-intranet-eip
  vpc: tenant-b3c946b5-7eb3-4feb-9f82-79712062ca5c/vpc-e413becb1f4a46468f058e32f33a6305
  cidrs: [10.83.1.0/24]
  disable: false
---
apiVersion: networking.kubercloud.com/v1
kind: EIP
metadata:
  namespace: tenant-d87c4dbd-04fa-4066-9ef6-c6fa4217f316
  name: vm-mvp-01a168ce-tenant2-intranet-eip
  labels: {vm-mvp-test.ani.io/run: 01a168ce}
spec:
  subnet: kcn-system/vm-mvp-01a168ce-intranet
  ipVersion: IPv4
---
apiVersion: networking.kubercloud.com/v1
kind: Snat
metadata:
  namespace: tenant-d87c4dbd-04fa-4066-9ef6-c6fa4217f316
  name: vm-mvp-01a168ce-tenant2-intranet-snat
  labels: {vm-mvp-test.ani.io/run: 01a168ce}
spec:
  eip: vm-mvp-01a168ce-tenant2-intranet-eip
  vpc: tenant-d87c4dbd-04fa-4066-9ef6-c6fa4217f316/vpc-c44c262a220748aab0e4b20558849409
  cidrs: [10.84.1.0/24]
  disable: false
```

执行前必须保持 kube-system UID `5277649d-e28d-4a0a-ae76-8354365c63bc`、第一 tenant namespace UID `09e58ecb-fe20-454f-bc7b-2da3c406f0ac`、task VPC UID `f7ae3b4e-2b90-4fec-bb90-67673780a83a`、task Subnet UID `c24a983c-f9f4-403d-9807-8164004000a3`；第二 tenant namespace UID `fa138fd5-1f35-4fa2-b561-db3d136b2404`、VPC UID `2f2e25c2-55e1-49bf-b284-4d7504b32f2b`、Subnet UID `29cdddaf-c09b-49df-9323-dd776d8ec929`；以及上述 default VPC/config 身份与目标范围一致。第二租户身份已在本轮 Fedora 只读检查重新核对；执行时仍重新核验。源盘、三个 NAD 与两 VM 为保护对象。不新增 EIPGateway、VLAN、VPCAttachment、Public pool，不启生产 worker。

影响：KCN 正常控制器为新 Intranet Subnet 在共享 default logical router 上增加任务端口，并为本任务两个 VPC 增加 uplink、必要路由及分别限定 `10.83.1.0/24`、`10.84.1.0/24` 的 SNAT；不修改既有共享对象 spec、镜像、进程或全局默认网络。新增转发状态触及共享路由平面，因此需要用户针对这五个对象、现有 router 身份、范围及探针明确批准，不能沿用 VM/NAD 创建或 runtime 维护授权。

验收：原生 Ready/observedGeneration 仅作准入，随后由两真实产品 Guest 运行严格 DNS getent/resolvectl 与唯一正文 HTTP 读回，核正文 SHA、服务端实际 peer 与请求；再次检查两个租户 VM 的唯一 Guest HTTP 按既有隔离规则拒绝。不得以 HTTP200/原生对象 Ready 替代 Guest 结果。其余跨节点/双租户网络矩阵仍单列完成，不将此两目标通过扩大为 Internet 全部通过。

回退和最终清理：依次按记录 UID 删除两新 Snat、待解绑后删除两新 EIP、最后删除新 Subnet；每步等待正常控制器清理，确认本次 NAT/route/uplink/default-router 新端口消失。任一未完成则保留其依赖和精确记录，不继续删父对象。不得强拆 finalizer、直接删 OVN 状态或动旧 pool/default VPC/config。必要时先清此外部前置再走 Gov 正常删除两个 task VPC；源盘/NAD/HTTP 服务各按原任务清理流程处理。

源码依据：Resource `internal/data/network/kc_intranet.go` 的 `renderIntranetPool`/当前 provider 校验；KCN `api/networking/v1/{subnet,eip,snat,common}_types.go` 的原生字段；Gov 正式 PlatformNetworkService 入口与精确平台权限。方案是未执行差异，不是新的当前台账或通过记录。
