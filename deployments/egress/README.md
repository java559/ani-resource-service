# Egress runtime prerequisites

The Network process registers the tenant and platform gRPC services, including
virtual switches and legacy device retirement, and runs their durable workers.
The independent default VPC egress service requires the identity guard below.
The process requires the
additional reads/writes in [network-rbac.yaml](network-rbac.yaml). Adapt its
ServiceAccount subject to the actual deployment; this template is not applied by
any test or build script. No actor or tenant metadata header grants egress access.
A trusted ingress must explicitly supply `biz.EgressCaller`; until that integration
is installed, egress RPCs deny access. Controlled test injection is not IAM proof.

## Node facts

Build the same source snapshot's binary and
[NodeFacts.Dockerfile](NodeFacts.Dockerfile) on the task's authorized remote host;
follow [remote execution](../../docs/remote-execution.md). Record the resulting
image ID/digest and tool versions. The image includes `ovs-vsctl` from Debian's `openvswitch-switch`
package and checks both required commands during the build; its entrypoint runs
only the Network collector, not an OVS daemon.
`scripts/render-node-facts --nodes nodes.json --image <image@sha256> --ovs-socket-uid <verified-uid>`
renders manifests only, from the intended cluster's captured node UID inventory.
The output creates one small probe per node, with a separate ServiceAccount that
can GET only that Node and GET/PATCH only its precreated facts ConfigMap. It cannot
write `kcn-config`, nodes, routes, or other probes' ConfigMaps. Node replacement
requires a new reviewed manifest; an old collector fails closed on changed UID.

The probe enters the node network namespace via `hostNetwork`, reads `/sys`,
`ip -j` link/address/IPv4+IPv6 route facts, and read-only OVS Interface listings.
For OVS-managed physical devices it also reads the actual containing bridge,
kc bridge external IDs and `ovn-bridge-mappings`. These prove existing kc wiring;
the node's managed-device annotation alone is insufficient. The collector keeps
the exact group network names in `KCBridgeNetworks`, and records a successful
global mapping read with `ovs_bridge_mappings_observed`. An explicitly observed
empty mapping is needed to prove physical cleanup; an omitted map or missing
collector is insufficient. Update all target collectors before relying on these
proofs; older documents cannot establish group wiring or final cleanup.
The OVS socket path and numeric owner UID must match the actual nodes; read them
with `stat -Lc '%u:%g:%a' /var/run/openvswitch/db.sock` before rendering. Select only
nodes with the specified socket owner, or render separate groups. The collector
runs as that UID and drops all Linux capabilities, without privileged mode or host
PID access. Root with all capabilities dropped cannot connect to another UID's
owner-writable socket. Do not change the shared socket's mode or add DAC override
capabilities to bypass this check. Network mutations are absent from the
collector's commands. Missing OVS facts do not
imply an unused interface. Each collection has a 10-second deadline and runs at
15-second intervals; failures leave the old collection timestamp unchanged.
Network rejects facts older than 60 seconds, duplicate/missing sources, changed
node UID, assigned non-link-local IPs, management/default-route interfaces
(including their internal OVS bridge), virtual interfaces, foreign masters or
bridges and incompatible VLAN occupancy. Correctly configured kc-managed devices
can be registered through the existing platform API after complete verification;
they retain their actual managed status and their existing physical configuration.

Applying the probe manifests only discovers facts. The current product flow is
[create a virtual switch, then create a layer 2 network](../../docs/specs/kcn-v09-underlay-flow.md).
Backend acceptance freezes full membership and reserves the device before its
worker writes only the owned Node group label. KCN owns takeover annotations.
The old Adopt entry point remains a compatibility/operations path; its
UID/resourceVersion guarded registration does not rewrite `managedDevices` for a
pre-managed device or overwrite a foreign binding. Exact legacy admission,
observation and retirement rules live in
[the specification](../../docs/specs/vpc-snat.md#41-underlay-的网卡发现与二层网络).
Live deployment and physical verification require the current task's authorization;
their actual status is recorded in the execution ledger.

## Platform default VPC egress

`network.platform_default_vpc_egress` enables the independent platform service
only for the configured cluster and the exact `kcn-system/kcn-cluster` VPC UID:

```yaml
network:
  cluster_id: <configured-cluster-id>
  platform_default_vpc_egress:
    cluster_uid: <verified-kube-system-namespace-uid>
    namespace: kcn-system
    name: kcn-cluster
    uid: <verified-default-vpc-uid>
```

Without the guard the service is not registered. Invalid configured identity
fails startup; a changed live identity fails admission and reconciliation. This
service does not adopt native EIP/Snat objects, modify default VPC/Subnet identity
or `natOutgoing`, or create tenant ownership for the system VPC. Its public pool
must pass the existing qualification and exact topology checks; it has its own
durable address reservation and inverse cleanup lifecycle.

## Intranet pools

[Intranet pool prerequisites](intranet.md) covers the default VPC identity,
existing destination routing ranges, independent evidence and allocation gates.
