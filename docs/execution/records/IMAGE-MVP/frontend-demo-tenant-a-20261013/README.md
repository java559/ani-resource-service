# tenant-a 前端镜像演示：手动操作记录

日期：2026-10-13，用户时间为 Asia/Shanghai。所有 API 驱动、镜像下载/推送、验证与构建在 Fedora 执行；本地只查阅、编辑和转运。

用户授权：为 172.16.101.10～12 集群的 `tenant-a` 手动准备镜像空间；平台仓库和租户仓库各推送两个镜像；将操作记录落盘。开通不作为本次普通用户前端操作，不修改角色、套餐或 API Key 权限。此记录只描述本次实际操作；长期当前状态仍在 [执行账本](../../../status.md#image-mvp)。

本文按阶段保留历史结果。最新服务部署与目录验证见文末[追加：OCI 修复与最新 main 部署](#追加oci-修复与最新-main-部署)。

## 首次准备结果和边界（部署前）

| 项目 | 结果 |
|---|---|
| 当前 tenant-a 镜像空间 | pass，`t-tenant-a-demo`，状态 available |
| 平台仓库两个镜像 Push | pass，源和 Harbor 读回 Digest 一致 |
| 租户仓库两个镜像 Push | pass，源和 Harbor 读回 Digest 一致 |
| ANI 平台目录登记 | pass，通过已定向验证的修复 CLI 登记；现有 Governance 列表读回 total=2 |
| ANI 租户目录登记 | fail，当前线上 Resource 返回 HTTP 409 / UNSUPPORTED_ARTIFACT |
| 前端真实页面演示 | not_verified；本次操作验证接口和数据，不冒称浏览器页面已通过 |
| Resource 兼容修复 | Fedora 定向 red/green 和构建 pass；用户明确选择暂不更新线上服务，候选仅保留 |

注意：镜像成功上传 Harbor 不等于已出现在 ANI 目录。租户登记的阻塞须解除，前端“我的镜像”才能查询出这两个演示项。

平台 BusyBox 登记 ID：`img_b677b416424740a7aef2988bbfe757a5`；平台 Nginx 登记 ID：`img_12a31f01cacc4ba59fa3cfb2c7f16223`。这两条已由 tenant-a 的真实登录身份经公开列表接口读回；租户列表目前 total=0。最终核对见 `evidence/final-result.json`，修复前后的读取历史保留在 `evidence/catalog-before-deployment.json` 等文件中。

## 固定环境和归属

- 集群 context：`kubernetes-admin@ani-lab`，API：`https://172.16.101.10:6443`。
- kube-system Namespace UID：`5277649d-e28d-4a0a-ae76-8354365c63bc`。
- 节点：ani-01 / ani-02 / ani-03，分别为 172.16.101.10 / .11 / .12。
- Harbor：`https://172.16.101.10:30003`，实读版本 `v2.15.2-a97e7b83`。
- Harbor CA PEM SHA256：`7371905a16dd41d8059629967a6c65f1d49b3a73dca44ee5e2dddef159168ac1`。curl 和 skopeo 均校验证书；未使用 insecure 选项。
- Image installation ID：`497fdb5a-1395-416d-8e26-35c21e8d5893`。
- Governance 租户：code=`tenant-a`，id=`1`，管理员 username=`admin` / user_id=`3`。
- 当前权威 Resource 租户 UUID：`46936e6b-3a3b-4868-8d88-6833665abb70`，来自实际 Governance 数据库 `sys_tenants.resource_tenant_id`。
- 平台空间：`29fe8cdf-1b6c-4efd-aaa2-23de9a4436ac`，Harbor Project=`ani-platform-images`，Project ID=`5`，原本私有、available 且没有镜像登记。
- 本次租户空间：`0d4cd79b-0cb8-4585-8d46-0e8b2eaf041b`，Project=`t-tenant-a-demo`，Harbor Project ID=`11`，pull generation=`1`，space version=`3`。最终 SQL 只读确认该 space 仍绑定当前权威租户 UUID。
- 旧 `t-tenant-a` 归属 UUID=`08b021f2-f921-4521-b39b-5265ec81f00f`；与当前租户映射不同，未认领、修改或删除。

完整指纹、部署镜像引用和允许范围见 `evidence/profile.json`。原线上 Resource 源版本为 `a57b6760bf2dd7dd7e682d340db226037f529d5c`；Governance 本次以实际 Deployment 镜像引用记录，不把旧构建目录的源码 SHA 当成当前部署证明。

## 四个演示镜像

均为官方镜像的单平台 linux/amd64 内容；源 Tag 先解析为固定 Digest，再下载和推送，未构建替身镜像。

| 目录 | Harbor 引用 | 镜像 |
|---|---|---|
| 平台 | `172.16.101.10:30003/ani-platform-images/demo-busybox:v20261013` | BusyBox 1.37.0 |
| 平台 | `172.16.101.10:30003/ani-platform-images/demo-nginx:v20261013` | Nginx 1.28 Alpine；该源 Tag 实际内容版本为 1.28.3 |
| 租户 | `172.16.101.10:30003/t-tenant-a-demo/demo-busybox:v20261013` | BusyBox 1.37.0 |
| 租户 | `172.16.101.10:30003/t-tenant-a-demo/demo-nginx:v20261013` | Nginx 1.28 Alpine；同平台目录内容 |

BusyBox 固定 Digest：`sha256:66a6306db78bf2dbf3487f293aa8d6990d8e506fdffab9cc43fe422becf886e4`。

Nginx 固定 Digest：`sha256:0dcc88822d45581e65ae329f8be769762bf628d3b2bb7d2a077d4aa5c98b30e3`。

推送前检查目标 Tag 不存在；推送用 `--preserve-digests`，用 skopeo manifest SHA256 与 Harbor artifact API 双重读回。既有 Tag 没有被覆盖。源 index 与选定 manifest 的完整摘要见各 `*-source.json`。

## 手动操作顺序和对应接口

Fedora 任务目录：`/home/chabking/ani-image-mvp-runs/demo-tenant-a-20261013`。实际执行 helper 为 `/tmp/ani-image-demo-20261013.py`；归档副本为 `manual-driver.py`。下列 `$RUN` 指该任务目录，`$PRIVATE` 为其 0700 私有目录。

1. 只读核对节点、Namespace UID、部署配置、数据库真实租户映射、Image spaces/credentials/registrations 和 Harbor Project。现有部署已启用 Image，并配置 Governance→Resource 的 mTLS。
2. 用实际用户提供的 tenant-a 管理员凭据调用 `POST /api/v1/auth/password/login`。用户名和租户编号正常传入；密码从私有文件读取，响应 Token 只用于私有 Authorization header 文件。早先以初始化管理员凭据尝试租户登录返回 401，未猜测或重置用户密码。
3. 手动调用开通接口：

   ```http
   POST /api/v1/images/space:enable
   Content-Type: application/json

   {"slug":"tenant-a-demo","idempotencyKey":"image-demo-20261013:tenant-enable"}
   ```

   HTTP 200，返回上述 `t-tenant-a-demo` 空间及 available。创建拉取 Robot 后才返回成功。
4. `GET /api/v1/images/publisher-credential` 确认 not_issued / version=0，再调用：

   ```http
   POST /api/v1/images/publisher-credential:issue
   Content-Type: application/json

   {"idempotencyKey":"image-demo-20261013:tenant-publisher","expectedVersion":"0"}
   ```

   首次发布身份 active、generation=1、version=2，有效期至 `2026-11-12T07:42:41Z`。签发 Secret 只保存在私有交付文件和 skopeo authfile。
5. 平台空间已经存在且归属符合部署配置。先确认没有 platform publisher，再通过原 Resource 二进制执行首次签发，不轮换现有身份：

   ```bash
   "$RESOURCE_BINARY" -conf "$PRIVATE/resource.yaml" \
     -image-operator-config "$PRIVATE/operator.json" \
     -image-admin=issue-platform-publisher \
     -image-admin-input "$PRIVATE/admin-request.json" \
     -image-secret-output "$PRIVATE/platform-publisher-delivery.json"
   ```

   输入为 `idempotency_key=image-demo-20261013:platform-publisher`、`expected_version=0`、`rotate=false`。平台 publisher Robot ID=7、generation=1、version=2；Secret 没有发给工作负载或普通用户。
6. 用 skopeo 按固定 manifest Digest 下载两份源镜像，再分别用平台／租户 publisher 推送。命令结构为：

   ```bash
   skopeo copy --preserve-digests \
     "docker://<官方镜像仓库>@<已核对的linux-amd64-digest>" "dir:$RUN/images/<repo>"
   skopeo copy --preserve-digests \
     --dest-authfile "$PRIVATE/<platform-or-tenant>-auth.json" \
     --dest-cert-dir "$PRIVATE/registry-cert" \
     "dir:$RUN/images/<repo>" "docker://172.16.101.10:30003/<project>/<repo>:v20261013"
   ```

   实际四次 Push 均 exit=0，Digest 均读回一致。真实完整 argv、时间和 Harbor 回执见各 `*-push.json`。
7. 平台镜像用受控 `-image-admin=register-platform`；租户镜像用 `POST /api/v1/images/registrations`。请求填写镜像引用、显示名称、用途 `container`、accelerator=`none`、说明和固定幂等键。原代码首次平台登记 exit=1，租户首次登记 HTTP 409，原因均为 `UNSUPPORTED_ARTIFACT`；未用 SQL 直接插入登记或伪造平台信息。

   完成下述定向修复验证后，用候选 CLI 按相同 actor、请求和幂等键重试平台登记，两条均成功。此步骤只运行受控运维命令，未更新线上 Resource Deployment。
8. 前端读取使用这三个接口：

   | 页面操作 | 接口 |
   |---|---|
   | 打开“我的镜像”，显示仓库信息 | `GET /api/v1/images/space` |
   | 查看平台镜像目录 | `GET /api/v1/images/registrations?scope=platform&limit=20` |
   | 查看我的镜像目录 | `GET /api/v1/images/registrations?scope=tenant&limit=20` |
   | 点击镜像详情 | `GET /api/v1/images/registrations/{image_id}?scope=platform` 或 `scope=tenant` |

   前端本次不需要开通按钮，只读取已经准备好的空间。最终目录结果必须以本次读回证据为准。

命令沿用互斥锁 `/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock`，systemd scope 预算为 2 CPU / 2300M / swap=0。数据库和 Governance 临时访问经 Fedora loopback port-forward，通过既有 kubeconfig 校验 Kubernetes API TLS；数据库凭据未放在 argv。

## 登记阻塞与已准备的最小修复

真实 Harbor 对两个镜像都返回 `type=IMAGE`、`manifest_media_type=application/vnd.oci.image.manifest.v1+json`、`artifact_type=application/vnd.oci.image.config.v1+json` 和确认的 linux/amd64。原 `readRunnablePlatforms` 把所有非空 `artifact_type` 拒绝，误拒绝标准容器镜像。

[Harbor 2.15.2 原始模型](https://github.com/goharbor/harbor/blob/v2.15.2/src/pkg/artifact/model.go) 明确区分 Type、ManifestMediaType、ArtifactType；本次判定同时依据该模型和真实返回字段。

本地隔离工作树：`/home/chabking/.codex/worktrees/image-harbor-artifact-type/ani-resource-service`，基于线上 Resource 的精确 `a57b6760bf2dd7dd7e682d340db226037f529d5c`，避免把其他 main 增量一并部署。

- 仅修改 `internal/data/image/harbor_artifact.go` 和其现有行为测试 `harbor_test.go`。
- 允许明确的 OCI / Docker image config 类型；继续拒绝其他制品类型、Chart、attestation、未知平台、平台不匹配、过深索引和循环引用。
- Fedora 新增正例在原代码上失败；修复后 `go test -mod=readonly ./internal/data/image -run TestHarbor -count=1 -v` pass，原有相关拒绝用例 pass；Resource 二进制构建 exit=0。
- 未提交、未推送，未以本次定向检查冒称整库门禁通过。
- Patch SHA256：`ab03dfc62dd53f59c4f76c875d32ac1b49bfb23b5adec1fc20c82bc81fc0b2cf`。
- 候选 binary SHA256：`6096790aae445508450494fc1406f25e4b0489ac6e0811cef35d700b5976ec66`。
- 候选镜像 Digest：`sha256:bd1fb354c28670296812bab69b6a8bbd7f09c3ff9155ae6709a7d836efbb4e63`。
- 候选 Deployment patch 只替换 `ani-system/ani-resource-service` 的 `server.image`；不包含配置、数据库迁移、权限或服务注册变化。用户随后明确选择“仅保留已完成操作，暂不更新服务”，因此没有导入该服务镜像到节点、没有 patch Deployment。

最终只读核对 Resource Deployment 的容器名、镜像引用、imagePullPolicy 与操作前完全一致。候选修复和 OCI archive 留在 Fedora 本次目录；未提交或推送代码。若未来另获部署授权，应先核对该时点线上版本与此补丁是否仍适用，再按原幂等键登记；当前不自动执行。回退必须保留空间、凭证、镜像和 command。

## 凭据和保留

演示空间、四个镜像、对应 Robot 和目录登记供用户继续演示，保留。旧 `t-tenant-a` 和其他资源保持原归属。

任务收尾已删除本次暂存的用户密码、初始化密码、JWT header、Harbor 管理密码/header、DSN、AES keyring、cursor key、临时 HTTP request/response 文件；两个 Fedora loopback port-forward 已主动关闭。平台和租户 publisher 的交付文件及 skopeo authfile 保留在 `$PRIVATE`，目录0700、文件0600，便于授权操作者复用；不进入本记录或归档证据。文件名清单见 `evidence/transient-cleanup.json`。

本次一次只读配置核对的工具输出误含数据库连接密码，已告知用户；本记录不复制该值，未擅自轮换共享数据库账号。该历史工具输出不能被此脱敏文档视为已经删除。

## 追加：清理旧镜像测试数据及唯一空间限制核验

2026-10-13 17:26（Asia/Shanghai），用户明确说明旧 UUID 对应的是测试遗留数据，并授权删除；同时询问每租户最多一个空间是否实际生效。以下是该后续操作，不改写上面的历史保留记录。

删除前重新核对：旧 UUID 不属于当前任何 Governance 租户；空间 ID=`f66a5727-128d-4d8c-ac86-8cc84c6198ee`，Harbor Project=`t-tenant-a` / ID=`6`，为空；唯一凭证是 pull Robot ID=`5`；唯一 command=`23c23af4-ccd8-4568-bae6-76e17cdf6053`，已 succeeded/completed；没有镜像登记、Pod/控制器引用或使用该 Robot 的 Kubernetes dockerconfig Secret。Robot description 中的 installation、space、purpose、generation、command 均与数据库一致。

明确保留当前 `t-tenant-a-demo`、`ani-platform-images`、两条平台登记、其他 Image 行、Governance 租户和权限、Resource 部署。`tenant-08b021f2-f921-4521-b39b-5265ec81f00f` Namespace 由 Network 管理，保留，不把它当作镜像空间删除。

实际执行顺序：

1. 将这组旧 Image 行备份到 Fedora 0700/0600 私有路径，公开证据只记录 SHA256，不包含凭证密文。
2. 做实际数据库重复插入检查：使用当前 tenant-a 的同一 UUID、不同 space_id 和 Project 名尝试第二个空间；确认由 `image_space_one_per_tenant` 拒绝，SQLSTATE=`23505`。子事务自动回滚，检查结束当前租户仍为一条、probe 行为零。索引 `indisunique/indisvalid/indisready` 全为 true。
3. 用短事务和原空间 advisory lock 将精确旧空间标记 blocked / OPERATOR_CLEANUP，CAS 匹配原 version=3；事务结束后才访问 Harbor。
4. `DELETE /api/v2.0/robots/5`，GET 读回 404；随后 `DELETE /api/v2.0/projects/6`，GET 读回 404。Project 为空，没有删除镜像层或触发 GC。
5. 在一个短事务中仅按旧 `space_id + tenant_id + owner_scope` 删除关联 registration、credential、command 和 space。实际删除为 0/1/1/1 行；没有给业务 runtime 增加 DELETE 权限。
6. 读回四张表的旧范围均为零，并比较保留对象前后快照一致。临时 Harbor 管理 header、cookie、响应文件和 CA 副本已清理。

结果：旧镜像测试空间、Robot 和空 Project 已删除；当前 Image 库剩一个租户空间 `t-tenant-a-demo` 和一个平台空间 `ani-platform-images`。Governance 仍为 tenant-a、tenant-b 两个业务租户；tenant-b 尚未开通镜像空间。Resource 未更新，原 OCI 登记阻塞仍保持原结果。

“每租户最多一个空间”按不可变租户 UUID 强制，而不是按 Project 名字相似度判断；旧记录与当前 tenant-a 使用不同 UUID，所以原来的两条记录没有违反唯一索引。

执行 helper：[cleanup-driver.py](cleanup-driver.py)。脱敏盘点、保留对象前后对比、HTTP 删除回执、数据库删除回执及实际唯一约束检查见 [cleanup-evidence](cleanup-evidence/result.json) 和 [唯一约束检查](cleanup-evidence/one-space-per-tenant.json)。Fedora 运行根为 `/home/chabking/ani-image-mvp-runs/cleanup-old-space-20261013`，沿用原重任务锁与2 CPU/2300M/swap0预算。

受限恢复快照仅在 Fedora：`private/old-image-rows.json`，不进入仓库。它用于溯源，不能直接恢复已删除的 Harbor Robot/Project ID；未来恢复必须重新核验归属并建立新受控绑定，不能把旧 UUID 自动认领为当前 tenant-a。

## 追加：OCI 修复与最新 main 部署

2026-10-13 用户随后明确授权“把这个问题修了，然后更新最新代码到10～12环境”，取代此前暂不部署的决定。部署完成时间为 17:50（Asia/Shanghai）。本次沿用同一集群、当前 tenant-a UUID 和四份演示镜像，没有重新开通空间或轮换发布凭证。

最新 main `97b13968d156d65fe492cb583ec9920d21d0e57a` 已含 OCI 修复，来源为 `2119a0b`：`supportedImageArtifactType` 按 manifest 类型匹配 OCI/Docker image config，保留旧 Harbor 空类型兼容；制品类型不匹配、Chart、attestation、未知平台、索引深度和循环限制继续生效。本次部署完整最新 main，未使用前述 a57 基线的未提交候选。源码隔离工作树为 `/home/chabking/.codex/worktrees/image-registry-update-20261013/ani-resource-service`，部署前再次通过 `git ls-remote origin refs/heads/main` 核对固定 SHA。

Fedora 运行目录：`/home/chabking/ani-image-mvp-runs/update-97b13968-20261013`。源码使用该提交的 `git archive`，归档 SHA256=`50a543220c962cc22a25c069467b7db14ff30d9708a398c8d08f42b36951187e`。未覆盖其他工作树，旧候选仍保留。

实际操作顺序：

1. 再核对 kube-system UID、三节点、Resource Deployment、其他服务镜像、当前 Image 空间、Network schema=13 与全部已应用迁移的 checksum。Network 操作只有 22 条 succeeded，无待执行操作。Instance 未配置 `ANI_INSTANCE_CONFIG_FILE`，继续关闭。
2. Fedora 固定 Go 1.26.9，在原共享 flock 和2 CPU/2300M/swap0预算下执行以下必要验证并构建；均 exit=0。没有以此冒称整库 `make verify` 或全量集成测试通过，本次未创建代码提交。

   ```bash
   go test -mod=readonly -count=1 ./internal/data/image ./internal/biz/image \
     ./internal/service/image ./internal/server ./cmd/ani-resource-service
   go test -mod=readonly -count=1 ./internal/biz/network \
     -run 'TestLB(EmptyListeners|DeferredProjection|ProjectionPreserves|RedirectProjection|ListenerDeployment)'
   go build -mod=readonly -trimpath \
     -ldflags '-s -w -X main.Version=97b13968d156d65fe492cb583ec9920d21d0e57a' \
     -o "$RUN/bin/resource" ./cmd/ani-resource-service
   go mod verify
   ```

3. 新版需要 `0014_lb_deferred_activation.sql`。先用 `pg_dump --format=custom` 私有备份 Network 数据库，再在现有数据上用短事务执行该迁移、`SET CONSTRAINTS ALL IMMEDIATE` 并 `ROLLBACK`；检查所有既有 checksum 和 schema 仍为13。迁移试运行 pass。
4. 构建保留 CA、许可证与源码 revision label 的 scratch 镜像，导出 OCI archive。服务镜像引用：`localhost/ani-resource:97b13968d156-20261013@sha256:ca315d144391cd2a186ad23637eb1427ff95c9732a343c06a1f57f60c3c805c1`。binary SHA256=`15c6e96776f2b523beb437824f6b1d72ba57e4ec747fe59d782718295cc4d7ae`；archive SHA256=`61aea295dcee9088f87f087b40a5971d0ff2e7ee5c56202cdc1eb7c24ebdb5b4`。
5. 逐台导入 ani-01/02/03 的 containerd。临时辅助 Pod 仅挂载 ctr 二进制、containerd socket 和任务 EmptyDir，无 ServiceAccount Token。ani-system 的 restricted 策略拒绝 hostPath，故改用既有 kube-system；没有修改 Namespace 安全标签。复制使用 `kubectl cp --no-preserve=true`，逐台验证归档 hash 和镜像 manifest digest。
6. 首次迁移 Job 未能启动：CRI 将带 tag 的 digest 引用规范化为 `localhost/ani-resource@sha256:…`，而最初只确认了带 tag 的完整名称。撤销该 Job，确认 schema 仍为13，恢复旧 Resource 到1副本。随后补齐三台节点上的规范化引用；每台用新版镜像启动一次 `-h` 检查容器，均退出0，再继续部署。失败与恢复记录见 [first-deploy-recovery.json](update-97b13968/evidence/first-deploy-recovery.json)。
7. 第二次将 Resource 缩至0，等待旧 Pod 退出后再做停机备份；使用新版官方 `-migrate` Job 和既有 `resource-network-owner` Secret 执行 owner 迁移，运行角色为 `ani_resource_network_runtime`。Job Complete，读回 schema=14。随后仅替换 `server.image` 并恢复1副本，rollout pass。Deployment 最终 spec 与原 spec 比较只差镜像；其他服务镜像、现有配置和角色/套餐不变。迁移按官方规则为新表配置 runtime 权限。
8. `/healthz`、`/readyz` 均 HTTP200；实际启动日志的 `service.version` 为上述完整 SHA。使用用户提供的 tenant-a 管理员凭据登录，通过原公开 `POST /api/v1/images/registrations` 重试原两个幂等键。两个标准 OCI 镜像均 HTTP200；重复同键返回原登记，未产生重复项。没有用 SQL 插入镜像登记。
9. 同一登录身份查询空间、平台列表、租户列表及四条详情；两个列表均 `total=2`、items=2，详情 Digest 与原 Push 结果一致，linux/amd64 平台正确，空间仍归属当前 tenant-a UUID。原 OCI 登记阻塞已解除。
10. 删除本轮密码、JWT header、临时请求/响应文件；终止两个精确 port-forward 并确认端口27788/29091关闭。所有本轮 import/check Pod 和 migration Job 均已删除。演示空间、四个镜像、原发布凭证和四条目录登记保留。

新增租户登记：BusyBox=`img_5eb2226977134c09a330ad329fb32d9f`；Nginx=`img_8a283065022c4120a1c10fdc357ec8f5`。平台仍为前述两条。

真实登记/查询 pass，服务部署 pass；浏览器页面、容器创建/拉取及完整 Notebook 业务链仍为 not_verified，本轮未启用 Instance。三节点均验证镜像可启动；Resource Deployment 保持原来的单副本。

可复现操作与脱敏证据：[build.sh](update-97b13968/build.sh)、[image-build.py](update-97b13968/image-build.py)、[deploy-driver.py](update-97b13968/deploy-driver.py)、[api-driver.py](update-97b13968/api-driver.py)、[部署结果](update-97b13968/evidence/deployment-result.json)、[目录验证](update-97b13968/evidence/frontend-api-verification.json)、[清理结果](update-97b13968/evidence/cleanup.json)。API helper 复用本文已归档的 `manual-driver.py`，固定请求和路径以脚本为准。

恢复条件：旧 a57 服务要求精确 schema=13，因此迁移14后不能仅回退 Deployment 镜像。停机前最新备份为 Fedora `$RUN/private/network-schema13-before-migration.dump`；其 SHA256/时间见公开 [备份摘要](update-97b13968/evidence/network-schema13-before-migration.dump.json)。数据库恢复必须停用 Resource、评估并保留迁移后的业务写入，再恢复 schema13备份与旧服务；本轮没有执行恢复。原 Deployment 完整快照仅存 Fedora private，备份和配置快照不进入仓库。

发布归档中，终端导入日志仅去除行尾空白；历史候选补丁的原始内容以 [repair-patch.json](evidence/repair-patch.json) 的 `patch` 字符串保存，解码为 UTF-8 后可按同文件 SHA256 复核。原始 `.patch` 仍保留在 Fedora 历史运行目录。

提交前的 Fedora `make verify` 发现并修复根 `buf.yaml` 的排除路径：`instance` 改为工作区相对的 `api/instance`。完整门禁、凭据扫描、归档语法和文档链接检查最终均 pass；先前配置及校验环境失败原样记录于 [publication.json](update-97b13968/evidence/publication.json)，最终输出见 [publication-verify.txt](update-97b13968/evidence/publication-verify.txt)。该校验通过后仅追加本段和验证证据，没有再改运行源码。
