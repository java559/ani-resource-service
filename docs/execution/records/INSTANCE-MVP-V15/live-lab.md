# 2026-10-13 等待 GPU 期间的真实实验

本文索引一次固定实验的来源、命令与 receipt；当前完成度只在 [status.md](../../status.md#gpu-container-instance-mvp-v15) 维护。全部执行在 `ssh fedora`，任务根 `/home/chabking/workspace/ani-resource-service-runs/instance-mvp-v15-20261013`；沿用原共享锁、Go 1.26.9、2 CPU/6 GiB 和私有 workspace/cache。没有提交、推送、共享服务部署或前端修改。

## 固定来源与源码

- Volcano scheduler 1.15.2、旧 1.12.0 与独立 device plugin 1.12.0 的 [固定来源 manifest](volcano-source-manifest.json)，SHA256 `0b536c57fcfd17b0fbf182ee247edefb6fde3efd77266fedfa14e756a0ffbe1e`。Acc 的比较文档为 `docs/runbooks/volcano-v1.15.2-compatibility.md`；旧版本限制是源码支持范围，不是用户指定部署要求或已通过硬件验收。
- Notebook schema、注解复制、STS 顶层无 ANI 标签及 ownerRef 限制见 [投影核验](notebook-v1.10-projection.md)；[来源 manifest](notebook-source-manifest.json) SHA256 `5afc17a94490a7664633addf31e6cbfe50e791956fc528f1a2a56428436e608c`。controller 镜像到上游 commit 的构建关联仍 not_verified。
- [四仓源码文件哈希](lab-source-manifest.json) SHA256 `87f2ba1319539a19fbeebd02fbc4dce169f6dbfe8eb9b946010bd4a84bb55a4a`，各基线保持原记录。[实际二进制和任务 runner 哈希](lab-artifacts.json) 不代表未发布模块已正式可下载。

## 命令与结果

| 入口（任务私有目录） | 退出码 | 证据与范围 |
|---|---|---|
| `acc-compat-verify.sh` 具名 baseline/render/parser | unit 0；PG setup 1 | [初次日志](acc-compat.txt)：PG Unix socket 路径超过 108 字节，未触发业务断言 |
| `acc-compat-recovery.sh`，仅 provider PG replay 与 Acc build | 0 | [恢复日志](acc-compat-recovery.txt)：两个版本的正式 reader、TLS/Unix 原格式和真实 PG；GPU/NVML 是边界样本 |
| `sg-cpu-verify.sh`；`go test -tags=instancelive ./internal/runtime/kubernetes -run '^TestNotebookCPUInfrastructureLive$'` | 0 | [CPU 日志](cpu-notebook-sg.txt)，125.21 秒：真实 Notebook/SG/Redis/mTLS/WS/exec/CSI、两次原生启停、同 PVC 文件保留与最终回收 |
| `notebook-projection-verify.sh` | 0 | [Resource 日志](notebook-projection.txt)：顶层映射/UID 归属、原四组真实 PG 行为与目标服务构建 |
| `INSTANCE_CPU_PROJECTION_ONLY=1` 执行同一 case | 0 | [投影日志](cpu-projection.txt)，52.91 秒：新 Notebook 的 Queue/mode/管理注解到 STS template/Pod、UID 链、最终回收；不重跑已通过 SG/启停文件周期 |
| `lab-db-prepare.sh`，受控迁移及官方 Gov `init` / `check` | 0 | [数据库日志](lab-db.txt)：五域真实 PG，无产品操作/完成状态 SQL 注入 |
| `lab-build.sh` | 0 | [构建日志](lab-build.txt)：空 GPU 目录 Resource composition、正式 Gov server；Acc/SG 复用已通过 artifact |
| `lab-db-credentials.py`、`lab-configure.py`、四个正式 daemon 与 `lab-api.py` | 0 | [API 结果](lab-api-calls.json)、[receipt](lab-live-result.json)：正式登录/当前权限/套餐、实验租户/角色、Gov→Acc 管理登记、Gov→Resource mTLS/PG 查询，四服务重启后的原键重放 |
| Image `-image-admin init-platform` | 0 | 生产 lifecycle 与真实 Harbor 建立本 InstallationID 的专属项目，state=available；没有注册 GPU 工作镜像或签发 publisher credential |

CPU 使用 SG 既有 container 路径，**不是 GPU InstanceBinding/Gov GPU 创建验收**。实际查询证明鉴权和服务接线可运行；空供给创建被拒绝，Gov/Resource 操作计数均零，不能代替 GPU 接受、运行、终端和退额。

## 资源与恢复

写入前 driver 重核实际 API `https://172.16.101.10:6443`、kube-system UID `5277649d-e28d-4a0a-ae76-8354365c63bc` 与 `.10/.11/.12` 节点地址，并对既有对象核对任务/租户标签。

CPU 原 Notebook/Pod/StatefulSet/PVC/PV 已按保存 UID 清理；[原启停 receipt](cpu-result.json) 和[新投影 receipt](cpu-projection-result.json)记录实际回收，没有直接删 PV/finalizer。CPU Namespace 与正式 Gov 创建的业务 Namespace 均保留，具体 ID 在 [lab-live-result.json](lab-live-result.json) / [lab-resources.json](lab-resources.json)。每个 Namespace 只有普通 `workload`、`session-runtime` SA 及 namespaced SG exec Role/Binding；没有新增全局 RBAC。Busybox digest 只作为 CPU 探针，不是 GPU 工作镜像。

正式 Gov 套餐 `instance-lab-only`、租户 `instance-lab-v15`、用户和九项 INSTANCE 当前角色权限由产品 API 创建。Acc 集群/池各一条、供给组/规格为零；重启后的原键重放没有增加对象。五域 runtime 非 superuser/bypassRLS/owner，十个 owner/runtime 角色最终使用独立凭据。最初共享角色密码已修正，修正后的正式服务启动/查询/重放重新验证通过。

验证后四个任务 unit 正常停止：inactive/dead，ExecMainStatus=0。持久容器 `instance-mvp-v15-lab-pg`（768 MiB/1 CPU）/`instance-mvp-v15-lab-redis`（256 MiB/1 CPU）、PG 卷 `instance-mvp-v15-lab-pg-data`、Redis AOF、角色密钥及私有配置保留。Harbor 专属项目 `inst-lab-215c5205-9749-42e6-8be0-ca1dab9007ba`，provider project 9，state=available；未改其他项目。

恢复见 [运行手册](../../../runbooks/instance-mvp-fedora.md#本任务隔离实验的恢复)。Fedora 最后 SSH 退出会停止 user manager，使用有界 `lab-session.sh` 保持实验会话，不修改 linger；结束已创建任务停止标记。所有 DSN、密码、证书私钥、Token 和登录输出只在 Fedora `private/`，不在本证据目录。

保留的失败包括初次 socket 长度、未转运 SQL 文件、实验 Image 项目名长度、默认管理员缺新权限的 403 及一次驱动 DTO 字段名错误。403 经正式 Role 更新只新增四项必要 Acc 管理动作，其余均限定修复。没有运行全库测试或重新审计无关服务。

## 剩余真实条件

实际 GPU/独立设备插件尚未就绪；scheduler 1.15.2 配置没有启用 `deviceshare.VGPUEnable`。平台提供后须固定实际插件/驱动/HAMi-core、Queue、F/K，完成已有硬件消费证明才能建立供给/规格，再冻结 GPU 工作镜像、拉取凭证和 quota。随后使用已准备租户执行共享 GPU 主链及对应整卡验证。Readiness、项目 available、查询与 CPU pass 均不提升为 GPU 主链完成；MVP-05 额外矩阵不增加为本轮前置。
