# 10–12 设备所有权维护单

当前操作授权：用户本轮提供既有节点SSH凭据引用，确认“现在没有别人使用了”；随后针对“再取得该具体变更批准”明确批注“批准了，直接变更，别问我了”。维护窗口为本次连续执行期间。本文件准备具体变更并记录授权，不将配置检查当作产品验收。

目标集群：API `https://172.16.101.10:6443`，kube-system UID `5277649d-e28d-4a0a-ae76-8354365c63bc`；执行驱动为 Fedora。节点顺序 ani-01/.10 → ani-02/.11 → ani-03/.12。containerd 2.3.4，systemd unit `/etc/systemd/system/containerd.service`，ExecStart `/usr/local/bin/containerd`，配置源 `/etc/containerd/config.toml`（version=2，原生自动迁移有效配置为version=3）。三节点 effective device ownership 均为 false。

唯一配置差异在原 version=2 的 `[plugins."io.containerd.grpc.v1.cri"]` 段增加：

```toml
device_ownership_from_security_context = true
```

保留全部其他配置，包括 ani-03 特有运行时设置。原配置 SHA-256：ani-01/02 `dc3a233b4002a4aea7e31eb59b0a93d624a58e21676e9bea582c81f49cf69f93`，ani-03 `a192311851c04172988c8dc06763b57d3f8a3cb9222065e460b495b9aa04208b`。仅配置摘要与当前指纹一致才写入；每节点备份至 `/var/backups/ani-vm-mvp-20261023-01a168ce/config.toml.before`，保留模式和属主。凭据仅存 Fedora 任务私有文件，不进入源码、快照、证据或日志。

执行器为 `scripts/vm-runtime-maintenance.py`：先验证目标kube-system UID；生成同目录候选，调用 `containerd --config <candidate> config dump` 验证新有效 runtime 段为 true，再原子替换并 `systemctl restart containerd`。重启后核实 active、有效配置、节点 Ready 与API readyz，成功后才进入下一节点。失败立即恢复该节点备份并重启，停止后续节点；不停止其他业务负载、不更改KCN或其他组件、不 drain/删除其他Pod。

限定后续探针：在本任务专属原生探针namespace和 vm-mvp 对象前缀下，核验已准备Ubuntu 24.04 qcow2身份，经原生CDI导入/克隆和 blank Block/RWO，观察非root CDI 设备访问；按候选节点调度原生VM后读取/写回盘marker。正式双租户产品链另走真实Gov授权/租户namespace。probe资源逐项登记UID，使用产品入口优先或原生身份前置删除，核实DV/PVC/PV回收；用户镜像和共享组件保留。没有原生盘及guest结果时 V02 仍为 NOT_RUN。

依据：[CDI v1.66.1设备所有权说明](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/doc/block_cri_ownership_config.md)、[containerd v2.3.4配置版本迁移](https://github.com/containerd/containerd/blob/v2.3.4/docs/cri/config.md)。
