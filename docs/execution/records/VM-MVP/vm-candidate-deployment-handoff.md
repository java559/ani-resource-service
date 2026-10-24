# VM 候选部署交接

这是 2026-10-24 编制的 VM-05B 可审部署输入。任务分支、正式模块和以下三服务镜像已发布；共享服务部署、main合入、tag/release均未执行。当前候选仅运行于本任务 Fedora 隔离目录，连接已核验的 `172.16.101.10–12` 集群（kube-system UID `5277649d-e28d-4a0a-ae76-8354365c63bc`）。一期完成与否仍只看[唯一执行状态](../../status.md#vm-mvp)和[验收合同](../../../verification/vm-mvp-acceptance.md)，本文件不复制验收矩阵。

## 当前可追溯产物与可获取依赖

以下 `$run` 固定指 `/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce`。二进制 SHA 是已有构建/启动回执中的实际值；PID 是交接时点记录，不是以后重启的身份授权。

| 服务 | 实际候选与 SHA-256 | 实际源码输入与启动记录 |
|---|---|---|
| Resource | `$run/resource-server.input19`；`9e5f6b0ed545757ca53957bf8d5a530f1220ce472f6f501097ecca353d4acc49` | 基线 `63e138174831ba7cf32452e6216143a147dadc82` 加完整task增量：stage05经06–15、stage16含16c/16d、stage17窄console、stage18/18b媒体，再[stage19构建](checks/resource-input19-build.json)4文件overlay，不能只取最新overlay。stage19 manifest `596d4c7add64d72b6c5432107cb12e67192b212c6fc1fc4276f54ed4b07dea6f`；[受审准备](checks/resource-input19-swap-prepared.json)→[实际swap](checks/resource-input19-swap-executed.json)PASS、该历史PID `2184281`，后续[V24同binary配置接线](checks/v24-live-prep-01/b-candidates-maintained-02.json)后当前PID `3305039` / start `70294373`。配置/预算与指定bytes守卫保持，swap本身VM改动0；后续USB输入是正常产品Start按UID/RV补齐，不能把swap前VM/VMI快照当当前。此前[18b构建](checks/resource-media18b-build.json)、[18b切换](checks/resource-iso18b-swap-executed.json)、[Windows目录重载](checks/resource-windows-reload-executed.json)保留历史来源。 |
| Gov | `$run/gov-server-guest`；`ce21216e60b78eae0eb56a27e76082e9438c8c1f961b96f66e4b7439d027752c` | Gov 基线 `a1dcdd5f14ee607e25131ce8816ff8d0a85faa76` 加 task dirty，Fedora `gov-stage-01` 经 `gov-generate-04.sh` 生成并构建，`gov-unit-05.sh` 验证敏感初始化路径；生成返回逐文件摘要 `$run/gov-return-04.json`。后续[V24同binary配置接线](checks/v24-live-prep-01/b-candidates-maintained-02.json)后曾为PID3305121；后续[owner客户端接线](checks/v24-owner-live-prepare-01/applied-03.json)仅增加17个ACC/Model/Inference客户端环境变量，同binary当前PID `3424470` / start `70793927`，启动记录 `$run/private/gov-candidate/process.json`。 |
| SG | `$run/session-gateway-gvk`；`7b58275ea629a0c3572d4224e5f07b7e05057a97d661a4d1062dd19a13612dfd` | SG 基线 `fb354938978250506cb22409e6ce8545d779ab4f` 加 task dirty，Fedora `sg-stage-01`，后续原生 GVK 适配修复；见[构建/重载回执](checks/sg-vnc-gvk-reload.json)。后续只续期同范围任务SA；[最新例行续期](checks/sg-task-token-renewal-3369384.json)固定当前PID `3440937` / start ticks `70865255`、token到期2026-10-24T13:02:52Z，active0/lease0与22项allow/deny守卫PASS，原binary/配置/PKI/ticket key及两namespace权限保持；V24两exec Role/Binding已在续期前正常回收。凭据后续到期须由部署owner重新取得实际记录。 |

后续[media18构建](checks/resource-media18-build.json)是stage17加 `internal/data/vm/media.go` 与测试两文件overlay，产生 `$run/resource-server.iso18` / SHA `e6b885178a330fb3c478804156ec3bb73d3f56cea53ae1cc1ba1fc410ea1fd57`。它修正VM自有Filesystem ISO副本host初始化与Guest只读CD-ROM的区别，保留Block host只读及历史对象兼容清理；见[S05](../../../specs/vm-mvp.md#s05-预置介质与设备默认值)。该18构建回执为 **CODE_PASS_LIVE_NOT_RUN / runtime_replaced=false**，当时未切换；后续18b实际切换由上表独立执行回执确认。不能用18构建文件名替代18b的完整来源，亦不把旧ISO失败回填成功。当前Linux ISO安装及系统盘首启进度只在唯一执行状态记录。

任务媒体目录中的Fedora44 ISO已以[就绪源](checks/fedora44-ready.json)/[单遍原ISO前缀](checks/fedora44-onepass.json)证明绑定指定PVC UID与全内容SHA；该源准备不代表ISO安装通过，实际阶段仅由唯一执行状态记录。历史[两租户五对象Intranet方案](network-connectivity-change-plan-20261024.md)SHA `00d2328cf6f9172472ebc69a79ddf69f4c6bc4601874abee143592780945c175` 从未执行；用户明确无办公内网环境、本轮不做该范围，现取消本轮应用，不能随候选部署执行。整体Goal已 **RESUMED**，公网问题 **DEFERRED_BY_USER**、不再发出网验收请求或修复；此前公网原生两对象子任务已正常清理，Guest出站原FAIL保留，结果只见[唯一状态](../../status.md#vm-mvp)，不构成Network API闭环或正式部署授权。

恢复后的必要证据入口：[Linux ISO系统盘首启](checks/product-fedora44-iso-v2-firstboot.json)已确认正常四步产品操作、v7/root唯一boot1/无INSTALL，浏览器已至About You新账户页，用户暂无时间亲自设置新凭据，此步及登录/marker为NOT_RUN_USER_UNAVAILABLE；任务私有可选ISO凭据未应用于Guest；[V3在线快照产品回执](checks/gov-linux-snapshot-v3-online-a-create.json)与[原生三卷链](checks/gov-linux-snapshot-v3-online-a-native.json)为Ready/COMPLETE、NoGuestAgent/crash-consistent，后续[五集合Restore](checks/gov-linux-snapshot-v3-online-a-restore.json)、显式Start与[三A实际读回](checks/gov-linux-snapshot-v3-guest-read-a.json)已取得，4条旧RBD trash仍待最终清理；[公钥初始化最终回执](checks/product-cloudinit-ssh.json)为真实key-only SSH/marker和新增proofVM完整回收。Windows已按批准接受EULA、VirtIO SCSI真识盘，原黑屏/只读帧历史保留。后续[正常四步原盘首启](checks/windows-firstboot-recovery-01/executed.json)各send1/succeeded至v9，原rootPVC/PV/CSI保持、root唯一boot1/无INSTALL、DRIVER及两unattached数据盘保留；新VMI4a9a6859…/launcher557f6711…。[真实BROWSER](checks/browser-windows-system-firstboot-account.json)已到Administrator新密码页，密码/确认均空，0新凭据/新许可且正常断流；人工设置及依赖登录/矩阵NOT_RUN_USER_UNAVAILABLE，不以原盘首启替代V15完整通过。最新SG身份见上表，其余矩阵继续，不预写结果；验收状态仍仅维护在[唯一状态](../../status.md#vm-mvp)。

当前完整Resource实现已在Fedora以`GOWORK=off`、正式console pin与短TMPDIR通过[完整make verify](checks/full-runtime-gate-01/resource-gate.json)，与input19业务实现字节相同；这不替代Gov/SG最终正式消费者与未发布远端镜像。两个后续纯API提交及各自完整门禁见[精确可审输入](checks/provider-next-01/publish-prepared.json)，已[精确发布两API分支](checks/provider-next-01/publish-executed.json)，Fedora正式module下载成功，消费者定向门禁正在收口。快照三个检查点实际进程退出与原操作恢复已由[PG+BOUNDARY测试](checks/snapshot-process-pg/snapshot-process-pg-01.json)通过，不外推成LIVE进程故障验收。

Resource已有本地实现提交 `8589b66f1370d8811777a87f3b02f6acba144818`，其[本地OCI构建回执](checks/resource-oci-8589b66f-01/resource-oci-built.json)固定manifest `sha256:f322e8f965f7f9373d33b35dd460c6312a2ce06c95a9573737e1516ff4d9f9b1`；原始build/help/inspect/export日志随回执保留。仅local构建与无网络-help检查，不等于业务启动/远端可拉取镜像。Resource实现ref已按用户持续code-push授权[普通SSH443发布](checks/resource-runtime-publish-01/resource-runtime-published.json)至`codex/vm-mvp-resource-runtime-20261023`且独立远端OID一致；Gov/SG最终实现ref亦已[精确任务分支发布](checks/formal-provider-02/implementation-published.json)：Gov `a64e0ab8ed6b4b17ca364ece044c719917f6e6d5`（`codex/vm-mvp-governance-impl-20261023`）、SG `c485419ab879fed9a2fad3a064fd328d829a2671`（`codex/vm-mvp-session-impl-20261023`）；三个精确远端镜像已于10:07–10:08Z经严格HTTPS Harbor发布并独立读回验证（完整引用见下表），Gov/SG已按这两个commit完成本地OCI：SG manifest `sha256:70bee379aa6410c6b5fb6e8dede3fd98ceeb0af7ad0d8c6b9baf386f14bd4773`，[实际回执](checks/consumer-oci-01/session/oci-built.json)；Gov manifest `sha256:271575fe4c397175a070acf57ca49d949a9fd83a24f0eae920f025f27e65d222`，[实际回执](checks/consumer-oci-01/governance/oci-built-attempt02.json)。Gov首次300秒静态编译预算中断原件保持，第二同源/同cache窗口完成；SG仅missing-config负向启动、Gov仅help，不是业务启动或部署。三OCI大包均保留Fedora，仅小回执/原始日志转运。候选task binary运行回执与下列镜像发布回执分别记录；远端镜像可取不等于已部署，也不能用本地目录、临时Go workspace或服务readiness代替依赖与业务验证。

Gov后续[单文件fixture提交及远端核验](checks/v24-coverage-01/fixture-published.json)已将同任务分支快进至 `b7112f7962e1aa23d903db5d557afdd5bb75af4d`，父a64；runtime/API/cmd/migrations/mod与a64相同，现有Gov OCI来源仍a64。三镜像已依用户“沿用以前方式”授权，经既有HTTPS Harbor成功发布；旧5000输入、project API403与NOT_FOUND分类失败原件保留，未切换Hauler共享配置或重启。

三个镜像的[实际发布与独立读回](checks/consumer-oci-01/publish-driver/harbor-v2/publication-sequence.json)均通过：各copy1次、0自动重试/覆盖/部署。现有既有身份和CA用于严格TLS，未创建权限或修改项目。发布记录中的镜像属于交付物，最终测试清理予以保留。

| 服务 | 精确远端任务标签 | 独立读回manifest digest |
|---|---|---|
| resource | `172.16.101.10:30003/icn-01a15337-release/ani-resource-service:vm-mvp-8589b66f1370-01a168ce` | `sha256:f322e8f965f7f9373d33b35dd460c6312a2ce06c95a9573737e1516ff4d9f9b1` |
| session | `172.16.101.10:30003/icn-01a15337-release/ani-session-gateway:vm-mvp-c485419ab879-01a168ce` | `sha256:70bee379aa6410c6b5fb6e8dede3fd98ceeb0af7ad0d8c6b9baf386f14bd4773` |
| governance | `172.16.101.10:30003/icn-01a15337-release/ani-governance:vm-mvp-a64e0ab8ed6b-01a168ce` | `sha256:271575fe4c397175a070acf57ca49d949a9fd83a24f0eae920f025f27e65d222` |

provider-only 产物 Resource `0a53b99c8d9f126fd02e6cdde2841c8fbea9046b` 新增独立 `github.com/zhangzhe-ctrl/ani-resource-service/api/vm/console` 模块及最小生成/验证接线；用户具体批准后，已[单次普通SSH443 push并独立核远端SHA](checks/provider-console-published.json)至 `refs/heads/codex/vm-mvp-console-api-20261023`。纯 console 模块不含 Root/biz/Kubernetes 依赖，不包含完整VM实现发布。

本轮用户已明确批准Windows实际许可页EULA接受，以及上述精确提交仅创建任务分支；Windows交互由Root继续，不预判安装结果。正式console版本 `v0.0.0-20261023185414-0a53b99c8d9f`（commit UTC2026-10-23T18:54:14Z）已由Fedora `GOWORK=off` [直接模块下载](checks/provider-console-module-resolution-02.json)确认可获取，Origin.Hash/Ref/Subdir及GoMod blob均匹配，module sum为 `h1:dC5v8SPszcnt5W0pl5rBOSCL85nDJnQJkjy1YcWFZLs=`。原[严格host-key前置失败](checks/provider-console-module-resolution.json)保留；修正只复用本地成功push已信任的公开hostkey到task-private文件，不复制私钥或改全局trust。实际push没有force flag、没有绕过安全hook或修改Git配置；该句属于原console批准时点；用户后来已持续授权任务代码分支普通SSHpush，最新边界见[Goal第4节](../../../plans/vm-mvp-complete-goal-20261023.md#4-连续执行授权及环境边界)。未执行PR/merge/main/tag/release、镜像发布或共享部署。

正式版本收敛的确切依赖边如下：

1. 纯 console API提交已按具体批准仅发布至上述任务分支，精确版本已可获取；供后续模块固定消费。此步未merge、推main或tag/release，用户后续已明确经检查的任务代码分支普通SSHpush无需逐次批准；合入main、tag/release、镜像发布和共享部署仍各守具体授权边界。
2. Resource 根、SG API/根和Gov已在工作树显式 require 这个精确 console 版本；[定向正式依赖回执](checks/console-formal-deps-01/console-formal-dependencies.json)区分Resource/SG API/SG根PASS与Gov旧SDK fetch失败边界。完整VM SDK及SG API现已各自门禁通过并按精确提交发布；正式消费者的最终落地见唯一状态，运行中旧候选来源不因工作树mod/sum更新而回填。运行中旧候选的workspace来源不因工作树mod/sum更新而回填。
3. SG 根模块 require 精确 console 和自身 API 版本，再发布 SG 实现。目标自身API为 `v0.1.1-0.20261024051540-811cc3839244`，移除自身本地replace；精确pin/tidy/verify已在隔离消费者通过，已按原SHA落地工作树、原HEAD/index保持；完整实现已发布 `c485419ab879fed9a2fad3a064fd328d829a2671`，只代表任务ref可获取。
4. Gov require 新 Resource 根 SDK 和新 SG API 精确版本。正式目标为 Resource `v0.0.0-20261024051201-01c079f1787f`、SG API `v0.1.1-0.20261024051540-811cc3839244`。Model API保持 `v0.0.0-20261021113912-a21befaaa5e5`；新依赖图严格tidy已移除不再使用的同提交Model根indirect；最终四mod/sum已落地并通过受影响build/tests。
5. 三仓必须在干净检出、`GOWORK=off`、无本地 replace 下取得这些版本，再按各仓既有门禁构建/校验并记录实际镜像 digest。运行中候选历史 `$run/gov-dev.work` 使用 Gov、Resource stage05、console 子模块及 SG API，并有 console `v0.0.0` 的版本限定 replace；它仅解释运行中历史候选/隔离验证来源，不进入正式提交树。[正式消费者闭包](checks/formal-provider-02/consumer-landed.json)已使用GOWORKoff、无本地replace并落地；SG验证workspace同理区分。传输专用SSH relay已关闭，不能将临时relay写成部署前提。

## 迁移、权限与持久密钥

Resource VM 的实际新迁移只有以下两份；磁盘、介质、backend 和快照当前记录使用已有 JSONB 结构，不编造额外迁移编号：

| 文件 | SHA-256 | 作用 |
|---|---|---|
| [`migrations/vm/001_vm.sql`](../../../../migrations/vm/001_vm.sql) | `3910788606c101c80672ca4e0eb90d3f862f0415b63aedc44d80d712ef391c52` | `vm` schema、VM/盘/操作/快照表，tenant 复合键/FK、幂等唯一键、持久工作索引。 |
| [`migrations/vm/002_guest_initialization.sql`](../../../../migrations/vm/002_guest_initialization.sql) | `75516940785913e8665b39cfd3345897f84a428715fee079f7756177146211b3` | 独立加密 guest 初始化记录和 tenant/VM 外键。 |

使用新 Resource 二进制的 `-vm-migrate`，输入仅为受管私有文件 `ANI_VM_OWNER_DSN_FILE` 与现存角色名 `ANI_VM_RUNTIME_ROLE`；实现见 [`cmd/ani-resource-service/vm.go`](../../../../cmd/ani-resource-service/vm.go) 和 [`internal/data/vm/postgres.go`](../../../../internal/data/vm/postgres.go)。迁移锁定 `84023001`，校验 owner 和版本 checksum；runtime 与 owner 必须分离。runtime 获 `vm` schema USAGE、schema_version SELECT、五张业务表 SELECT/INSERT/UPDATE/DELETE，不获 owner/DDL 权限。正常启动核对版本 1/2，不隐式迁移。

VM 必须与现有 Compute 使用同一个受限数据库配置，且 Network 的 tenant/namespace/Attachment 映射已存在。已有 Network 001–0018、Instance 与 Compute 迁移不因 VM 而重写；若目标环境尚未完成 Compute 实表迁移，应先按[原地迁移手册](../../../runbooks/compute-presets-migration.md)核验并执行其显式步骤，不能跳过或重播首建脚本。

Gov 本次 VM 没有新增 SQL schema migration：复用原 `sys_quota_operations` 的 action、canonical_request、CREATE 关联、幂等键和 outbox 字段；密码密文仍在受保护的 canonical_request envelope 中，不需要增设明文字段。目标 Gov 库按其已发布 `migrations/` 和 `app/admin/service/atlas.hcl` 进行 Atlas status/dry-run/apply，当前候选使用 Atlas v1.3.0，实际已应用至 `20261022010000_modeldev_execution_v21.sql`。不得把这份历史 ModelDev 迁移当成 VM 增量。owner 执行迁移，`scripts/ops/sql/grant-runtime-role.sql` 给 runtime public schema USAGE、业务表 DML、sequence USAGE/SELECT及 owner 默认授权；runtime 无 schema CREATE、owner membership、TEMP、superuser 或 BYPASSRLS。当前隔离库为 `vm_govlive_01a168ce`；Resource 为 `vm_resource_candidate_01a168ce`，互不读写业务表。

Gov API/权限目录是独立数据维护步骤：使用正式 `admin sync-apis --dry-run`/`sync-apis`、精确 VM/Compute/Network 权限登记路径，核对 `VMService`/`VIRTUAL_MACHINE` 与 Module 17；`scripts/ops/sql/register-vm-permissions.sql` 只登记 route/permission，不授角色、套餐或配额。既有全目录对齐的 19 条 baseline 差异未被豁免，发布前仍须处理或明确记录；不能直接 SQL 给租户/角色补权。具体套餐 Module17、VM/Network 权限和 VM-usage compute 选择由正常产品维护流程配置；VM 不新增 CPU/内存/磁盘 quota 账户或余额规则。

Gov `ANI_ACCESS_KEY_ENCRYPTION_KEY_FILE` 同时提供 guest envelope 的派生 AEAD/HMAC 根密钥；Resource `GuestCredentialKeyFile` 保护独立初始化表中的受保护密码 hash。两端密钥、已受理密文、原 key/body、spec digest 和 operation ID 必须随数据库备份一起保留且权限为私有。不得因重启重新生成密钥，普通 spec/operation/log 中不得放密码。SG ticket key、Redis DB1 的会话/幂等 tombstone 也须跨受控重启保留。

V24旧Instance回归已在同两任务候选进程接入独立Image/Instance与真实Quota listener，见[实际接线回执](checks/v24-live-prep-01/b-candidates-maintained-02.json)。Resource3305039/start70294373仍使用input19，`-conf`为任务私有`v24-instance-01/candidate-configs-v2`（原JSON对象仅追加Image语义字段），`ANI_INSTANCE_CONFIG_FILE`引用独立Instance配置；Gov3305121/start70294688同原binary，加Image39181/Quota39187与ConfigV2标记。原Compute/VM/Network配置与DB、三VM/native/卷/NoCloud/source/NAD身份保持。Image为隔离任务DB `vm_image_v24_01a168ce`，不连接共享Image数据库；现有Instance1–6和grants齐全，0InstanceDDL/GRANT。该配置用于任务回归，不是生产部署默认值，Image相关临时项目/robot/注册须待实际消费者Delete后正常回收。 后续Gov同binary owner客户端使用经身份核验的ACC `10.96.58.69:19090`、Model `10.96.44.130:19090`、Inference `10.96.166.234:19090`；保持原TLS server name、投影仅既有Gov三字段叶，当前3424470/start70793927。新增私有清理目录`$run/private/v24-owner-clients-01`待配置不再引用后回收；28.*旧别名未连接、0共享服务改动。CPU两例已正常Delete及物理回收，Image/SA/Secret/Harbor独立支持仍按[生产者清理交接](checks/v24-live-prep-01/v24-support-producer-handoff.json)保护；目录资格重读与GPU/Inference实际闭环分别以唯一状态为准。

Resource验收driver后续[独立发布](checks/resource-acceptance-followup-01/published.json)为 `356473726ef4192b0b1220be3fc532d1e9fab71f`（父8589，分支 `codex/vm-mvp-resource-acceptance-01a168ce`）：仅账户持久性、snapshot collector、tenant2 cleanup、Windows disks/lifecycle共5个脚本；535个runtime/API/cmd/migration/module文件与8589字节/模式相同，因此运行候选和已发布OCI仍精确使用8589，不重复构建。原WT HEAD/index保持，Windows未验项不由脚本替代。

## 实际入口与部署顺序

当前任务监听：Gov HTTP `127.0.0.1:39180`；Resource 业务 mTLS `39181`，SG 专用 console mTLS `39183`，admin `39184`；SG management mTLS `39182`、WS/health/metrics `39185`。Resource 服务 SAN `ani-network-service`、Gov→Resource leaf DNS `ani-governance`；SG SAN `ani-session-gateway`、Gov→SG leaf URI `spiffe://ani.internal/service/ani-governance`。CA/分离 leaf 仅通过私有文件挂载，见[本任务 PKI 回执](task-pki.json)。这些 loopback 端口和临时浏览器转发不是生产 ingress 设计。

1. 先取得正式依赖/镜像及经审配置，关闭新 VM 受理并备份数据库、配置和密钥，核实集群/namespace UID、来源 PVC UID/内容 SHA、Block/RWO SC、受管 NAD UID/config digest 和需保留的当前原生对象。媒体目录中的 verified capability 必须来自实际验证，不能按文件名推断。共享 runtime/gates、SC、网络变更仍需各自具体授权。
2. 停止旧执行者，等待原 lease 或保持其明确恢复边界，owner 显式迁移；记录每库各自提交状态。部署先 Resource（Compute/Network/VM/恢复清理 worker），再 SG（纯 console binding、最小 RBAC、稳定 Redis/ticket key），最后 Gov（Module17/action/codec/owner、耐久 CREATE/DELETE outbox、投递前删除栅栏）。不要求跨库事务，也不清除未知操作来获得空队列。
3. Resource 使用 `ANI_VM_CONFIG_FILE` 装配 VM；该私有 JSON 的 DatabaseDSNFile 必须与 Compute 相同，包含准确的 Media/Networks/Storage 和可恢复 guest 密钥。普通启动只持 runtime DSN。Network WorkerServer 必须同时装配 VM Stepper；检查 admin `/readyz`、数据库/schema/角色检查和 worker 恢复，随后用原 operation 查询确认真实持久工作接管。单有监听或 ready200 不代表 VM 可创建或清理。
4. SG 确认 management mTLS 身份、Resource 窄 console endpoint、任务/目标 namespace 的 VM/VMI/Pod 子资源 RBAC、票据 Origin 和运行时流许可。再启动 Gov，使其可达两端；保持现有加密根和 JWT 身份设置。用正常登录/`admin/me`、目录、原 VM/原操作查询和已审受理/恢复用例验证，不直连 Resource admin 替代产品链。
5. 只有新版本 worker 能恢复原 CREATE/DELETE、磁盘/介质/快照映射且删除仍经过真实物理清理后，才开放相应用户入口。Gov `ANI_VM_V1_WRITES=true` 开启新 CREATE；该变量仅控制 CREATE，不是全部直达变更的维护开关。开放还需实际套餐/权限、verified media、compute VM usage、tenant Network 准入及 SG 可用性分别满足。

## 回退与前端边界

回退先停止新受理；通过已审入口维护策略阻止新的用户变更，同时保留原操作查询、正确幂等重放、已受理 outbox 投递和原 owner 的恢复/删除能力。不能仅设置 `ANI_VM_V1_WRITES=false` 就声称所有变更已冻结。只允许回退到理解 VM v1 action/codec、受理删除栅栏、guest 密文、current media/boot、多盘映射、snapshot/restore 后新 PVC UID 和原 UID 清理记录的版本；未证明兼容的旧正式版不能作为回退二进制。

input19后，正常产品Start已按停止态/namespace/VM UID/resourceVersion补出固定USB tablet模板，依据[S08](../../../specs/vm-mvp.md#s08-电源与-vnc)。必要服务回退必须重新取得fresh维护窗口/进程与配置守卫，保留这些正常受理产生的模板增量；不能把swap前旧模板当回退字节写回，也不在线改VMI或自动重启来恢复设备。

新增V22v3通过二向CSI journal固定了临时PVC reservation，正常DELETE后精确目标/临时PVC消失、关联PV0，但[同归属base/temp RBD仍PRESENT](checks/import-delete-backend-v3-result/gov-import-delete-backend-v3-reservation-after-delete.json)，当时清理未通过；后续[05:51Z精确trash与既有image/journal测量](checks/import-delete-backend-v3-result/gov-import-delete-backend-v3-trash-final.json)确认正常延迟GC完成，新增用例回收通过。该新证据不回填删除前未观察到的imageID，不改旧v1归属缺口，也不授权手动RBD清理。

导入中删除V22的后端仍有[明确不可测边界](checks/product-import-delete-source-rbd-recovery.json)：已知source下两个temp child实际存在，但缺少CSI归属metadata，未能与原target/temp PVC UID建立image映射。保留原native absence与backend NOT_MEASURED，不把其它清理PASS扩大覆盖；无新可信PV/CSI身份之前，禁止归因、删除或flatten这些child，也不得把source残留视为已完成最终回收。

不降级 schema、删 outbox/账本、重置 expected_version、替换原 key、清空 closing/unknown 或直接删业务行。原生未知结果由理解该合同的 worker 观察接管；保持原 namespace/NAD/source/VM/PVC UID 关联和密钥。如果只回滚配置，必须对精确 PID/startticks/argv/binarySHA 和原配置字节执行受控 TERM/重启及可验证恢复，不能扩展到共享部署。

ANI正式前端由另一个owner负责，本任务不接入、不发布业务FE；已有正式FE原dirty和此前阶段成果原样保留交接，临时测试页不作正式FE交付。其接入合同是正常 Gov 媒体目录返回 `initializationCapability`/`initializationVerified`，仅对已验证 `CLOUD_INIT_NOCLOUD` 的 Linux `DISK_IMAGE` 显示可选 `guestInitialization{username,password,sshPublicKey}`；密码仅瞬态请求，不能进入 URL、持久缓存、普通查询或日志，不生成密钥对/私钥/Key 管理。ISO、NONE/unknown capability 拒绝初始化。ISO 受理为空白可写 Block 根盘、VM 独占只读 CD-ROM 副本、安装介质启动；用户安装与后续 `SYSTEM_DISK` 启动/弹出是正常独立意图。临时真实 VNC 页仅用于本轮浏览器验收，不是发布产物。完整字段规则引用[VM v1 合同](../../../contracts/vm-v1.md)，不在此复制 DTO 或验收结果。

交接文档自身不执行API/SQL、迁移或共享部署；正文链接分别标明已经实际完成的独立console API分支发布、定向依赖验证与产品验收，不把它们当其余实现发布。正式ref/digest已按上文实际发布回执固定；共享部署未执行，完整CODE/PG/BOUNDARY/LIVE/GUEST/BROWSER和最终清理仍按唯一状态补齐，未执行项保持NOT_RUN，不把任务镜像发布计为产品部署或一期完成。
