# Three exact OCI publications: prepared, not executed

The unchanged approval input is `frozen-publish-prepared.json`, SHA-256
`59054e929b454b8af8cfaeec428d7c3f9e8ec334503899df961f9b8bab85b096`.
This directory contains only the executor and copies of the three public build
receipts. No private authentication file is included.

`publish.py local-check --service resource` checks only local input hashes and
OCI metadata. The other service selectors are `session` and `governance`.
`self-test` exercises pure guards. Neither action contacts a registry.

Future publication, **only after the exact write scope, transport and auth mode
are approved**, runs on Fedora with one service per invocation:

```sh
python3 /home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce/consumer-oci-01/publish-driver/publish.py publish \
  --service resource --root-authorized --exclusive-new-task-tags \
  --approved-plan-sha256 59054e929b454b8af8cfaeec428d7c3f9e8ec334503899df961f9b8bab85b096 \
  --approved-driver-sha256 REVIEWED_DRIVER_SHA \
  --transport strict-tls \
  --auth-file APPROVED_EXISTING_FEDORA_MODE_0600_AUTH_FILE \
  --approved-auth-file-sha256 APPROVED_AUTH_FILE_SHA
```

Explicit approved anonymous access instead uses `--anonymous-authorized` and
omits both auth-file arguments. There is no `login` command, global config edit,
credential-helper invocation, credential output or credential copy.

Strict HTTPS and certificate validation are the default. The dormant
`--transport explicit-http --approved-insecure-endpoint 172.16.101.10:5000`
option requires separate approval. It uses skopeo's `tls-verify=false`, which
permits HTTP and disables TLS verification for this endpoint; it never follows
automatically from an HTTPS error. That mode is **not approved or executed**.

The executor checks the exact new target tag is conclusively missing, persists
the single copy attempt before issuing it, uses `copy --preserve-digests
--retry-times=0`, and independently reads the destination raw manifest digest.
Any existing tag stops publication. Registry V2 cannot provide atomic
create-only tag writes: the operator must reserve the exact new task tags
exclusively through the absence/copy/readback window. This runner never claims
an atomic server-side no-overwrite guarantee.

Any interrupted, failed or unknown copy is retained in mode-0600 task state.
Use the same command arguments with action `reconcile` to inspect the exact
destination; it cannot invoke another copy. Missing/unknown results require a
human decision and cannot be retried by this executor. Raw command output stays
in the task-private directory; public receipts expose only references and
digests. No deployment, service restart or VM action follows publication.
