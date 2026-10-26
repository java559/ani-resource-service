# Existing HTTPS Harbor publication channel

This independent v2 retains the three exact OCI archives, manifest digests and
new task tags. It changes only the destination host/project to the successfully
used `172.16.101.10:30003/icn-01a15337-release` channel. Original plan `59054e…`
and original publisher `f99ce6…` remain unchanged.

`source.diff` contains the complete publisher change. Harbor actions require
strict HTTPS, the pinned public CA in this task's `certs/ca.crt`, and exactly
`private/harbor-auth.json` mode 0600. HTTP and anonymous publication are rejected.
Only `ca.crt` may exist in the cert directory; no client key is copied.

The old temporary auth files were cleaned. The existing source Secret remains:
`ani-system/icn-01a15337-release-pull`, UID
`bfd0b923-2098-44bd-bb29-45e79af08b46`. This preparation queried metadata only.
No credential content has been read, materialized or copied. The historical
public preflights demonstrate this identity's pull/push grants in the same
project; current grants for the exact three new repositories remain untested.

Before publication, Root must approve credential materialization from that exact
existing Secret into this task-private file and a read-only TLS/auth/scope check
for the three destinations. Then use the reviewed v2 plan/driver SHA with
`publish --service resource|session|governance --root-authorized
--exclusive-new-task-tags --approved-plan-sha256 PLAN_SHA
--approved-driver-sha256 DRIVER_SHA --transport strict-tls
--auth-file EXACT_FEDORA_TASK_PRIVATE_PATH --approved-auth-file-sha256 AUTH_SHA`.
The publisher still refuses any existing tag, records a single attempt durably,
preserves digests and performs independent digest readback. Unknown results use
`reconcile` only. Registry V2 lacks an atomic create-only tag write, so the exact
new task tags must be reserved exclusively through absence/copy/readback.

No Hauler switching, systemd action, service restart, shared registry
configuration, deployment, new image build, merge or tag/release is part of this
path. This directory is prepared only; no registry request/write has occurred.
