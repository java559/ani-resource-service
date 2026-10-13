# 2026-10-13 真实 Image 接线与候选入口验证

这是后续固定实验记录；当前进度只由 [status.md](../../status.md#gpu-container-instance-mvp-v15) 维护。全部 API 驱动、格式化、构建与定向测试使用 `ssh fedora`、原任务根/缓存/互斥锁及 2 CPU/6 GiB 预算，没有全量测试或共享配置更新。

## 实际修复

真实 Harbor `v2.15.2-a97e7b83` 的正常 IMAGE 返回 OCI manifest、`artifact_type=application/vnd.oci.image.config.v1+json` 与确认的 linux/amd64 平台，见 [实际报文形状](image-artifact-shape.json)。旧 reader 拒绝全部非空 artifact_type，导致真实登记失败。现在仅接受 OCI/Docker manifest 对应的标准 image config 类型，保留空类型兼容；未知 artifact、typed index、chart、证明附件、平台矛盾及深度/数量限制保持拒绝。[定向 red](image-artifact-red.txt)、[green 和构建](image-artifact-green.txt)均记录原失败与修复，不新增版本矩阵。

Image 正式生成 `img_<32 hex>`，实例 ResolveCreateSpec 原先误按 UUID 校验。已改为调用 Image 的 `ParseImageID`；Gov 原授权/幂等用例及公开 HTTP 主链输入使用真实标识格式。对应 Resource production/fixture 重建、`TestInstanceCurrentAuthorizationOriginalReplayAndOwnerRelease` 和同一个 `TestInstancePublicHTTPPersistentIntakeAndCommands` 通过，见 [日志](image-id-joint.txt)。后者 45.14 秒，包含真实 Gov/Res/Acc PG、mTLS、SG/Redis/WS/原账，硬件/Kubernetes/exec 仍是原明确边界替身。

Image 运维命令将 config watcher 日志写入 JSON stdout，曾使真实驱动解码失败。现仅该模式日志改走 stderr，stdout 返回一个 JSON 结果；原登记键重放及解码通过。[构建](image-main-build.txt)、[保留的驱动失败](image-live-setup.txt)与后续真实登记证明该行为；没有重跑无关门禁。最终变更文件哈希见 [image-input.sha256.json](image-input.sha256.json)。

## 真实 Image 与凭证路径

实验租户在原 INSTANCE 套餐基础上，通过正式 API 增加 IMAGE 模块及四个精确权限：enable/read space、list/read registration。Gov 的原 Resource DNS-only mTLS 客户端接线启用现有 Image BFF，没有新运行服务。正式 `space:enable` 调用 Image lifecycle/PG/Harbor，建立实验租户空间及加密保存的 pull robot。

现有平台镜像按原 digest 在 Harbor 内复制到本任务专属平台项目，再由生产 `-image-admin register-platform` 登记。复制使用 [Harbor 官方 CopyArtifact 合同](https://raw.githubusercontent.com/goharbor/harbor/v2.13.3/api/v2.0/swagger.yaml)，实际 2.15.2 请求/读回验证成功；没有下载所有 layers、修改旧项目或发布 GPU 兼容声明。[复制 receipt](image-copy-result.json)、[正式租户授权→Gov→Resource Image 列表/详情](image-public-query.json)记录实际 digest 与 `img_…` ID。

真实 RuntimeImages 用例从受限 Image PG 与真实 Harbor 解析已登记 immutable image；Notebook adapter 从生产 Image 用例解密 pull material，并写入租户专属的 `ani-instance-image-pull` Secret。使用该 Secret 的实际 robot 对同一 private manifest 拉取通过，匿名 token 与其他现有私有项目访问拒绝，见 [凭证实测](image-pull-auth-result.json)。旧来源项目公开，因此未把访问公开来源误判为越权。没有导出用户名/密码、Token、dockerconfig 或密钥。

## 候选镜像失败与清理

候选为 [固定元数据](workspace-image-candidate.json)中的现有 `ani/kubeflow-jupyter@sha256:8a8d9a3eb0f4ed63702e873ba9658c49fa2e147c8f927a3fba196768f5d8e712`。Image 配置确认 `1000:1000` 与 `python /opt/ani/start.py`；这不是 shell、目录权限或 GPU 软件通过。

`TestRuntimeImagePrivateHarborPullLive` 使用真实 Image Runtime、受限 PG、私有 pull Secret、Notebook Controller 与 `ani-block`。没有命令覆盖、Jupyter token 注入、GPU plan、产品 CREATE、quota 或完成状态 SQL。实际 private image pull 成功，但容器反复退出，120.87 秒后启动验证 **fail**，见 [日志](image-live.txt)。因此未执行 shell/UID/工作盘写权限断言，也没有把候选标为兼容。

直接核对 image manifest 中最后几个小 layer，取得确切启动脚本：[入口 receipt](image-entry-result.json)记录 layer digest、源码 SHA256 和强制 JUPYTER_TOKEN 依赖。它不符合当前仅实例终端入口约定；本轮未绕过脚本或新增 Jupyter 业务接入。后续需要正式实例基础镜像，实际 GPU 软件仍待设备环境验证。

失败清理只按保存 UID 请求删除该 CPU 探针 Notebook/PVC，没有删除 PV/finalizer。最终 Notebook/StatefulSet/PVC/原 PV 均 absence，见 [清理 receipt](image-probe-cleanup.json)。租户共享 pull Secret、Image 空间/robot、平台登记/复制 artifact、PG/Redis 保留用于恢复；Gov/Resource 临时服务已正常停止，ExecMainStatus=0，fixture 容器无存留。

运行入口为远端 `private/lab-image-{inspect,copy,auth}.py`、`lab-image-{verify,live,joint,public}.sh`，私有配置仍只在 `private/instance-lab/`。live probe 必须明确提供 `INSTANCE_IMAGE_LIVE_CONFIG`，默认不运行；当前候选会正确失败。之前的 raw UUID 驱动 scope 失败见 [记录](image-live-id-scope.txt)，已按真实 Image ID 修正。
