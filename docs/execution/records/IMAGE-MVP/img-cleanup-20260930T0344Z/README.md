# 2026-09-30 用户要求的集群残留清理

本目录归档 2026-09-30 的实际清理结果。2026-10-13 补入 Git，仅补齐历史证据；当时的集群身份与资源检查不代表当前环境状态。

用户要求“把你在集群里留下的东西清理下”。本次仅针对前次 `imgp-0930-0200` 的精确资源、镜像 manifest/config digest；没有清理其他任务、共享数据或执行全局 GC。

前次 Namespace/Harbor 清理记录正确，但没有覆盖节点镜像缓存。此次发现 ani-01/02/03 尚有 5 个测试 Project 镜像引用及 3 个对应的 runtime image-ID 引用。

## 操作与结果

- Fedora API 驱动重新核对 cluster UID `87ecef8e-ac4e-442b-8e15-5e906263be6b`。两个测试 Namespace、三个 Harbor Project（同时按 ID 和名称）、五个 Robot 全部返回 404；全局 Pod 列表中没有测试命名空间、标签或镜像引用匹配项。见 [API 复查](api-recheck.json)。
- 节点运行时清理由本地 SSH 转运脚本、在三台实际节点执行。操作前验证所有同 digest 镜像引用均属于本次测试、没有其他 tag、没有使用者、没有测试 sandbox，也未标记 pinned；然后仅对已记录 image-ID 执行 `sudo crictl rmi sha256:30ecbe1509090b38ef3260f3609d64d8261230464eaa4d235901db496e9647fb`。
- [ani-01](image-cache-cleanup-ani-01.json)、[ani-02](image-cache-cleanup-ani-02.json)、[ani-03](image-cache-cleanup-ani-03.json) 的 CRI/containerd 复查均无对应镜像引用或 image-ID。实际退出码均为 0；脚本原件见 [精确缓存清理](image-node-cache-cleanup.py)。没有手动删除共享内容库、快照目录或执行全局 prune/GC。
- 临时管理凭证文件已删除，节点只读代理和两条 SSH 转发已关闭，节点监听端口与 Fedora 转发端口确认不存在。前次原始证据和其他任务的工作树保留。

最终 [summary](summary.json) 于 `2026-09-30T03:47:20Z` 记录 pass。[SHA256SUMS](SHA256SUMS) 在 Fedora 生成，回传后逐文件核对。这里只证明已记录资源和运行时缓存引用的清理，不声称 Harbor 后端历史 blob 或审计记录已被物理擦除。
