package data

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/network"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"
)

// NodeFactsCollector is a read-only infrastructure probe in the node network
// namespace, not a resource lifecycle worker. It never invokes link/route/OVS
// mutations. Its separate credential can patch only its precreated facts object.
type NodeFactsCollector struct {
	Client                                       dynamic.Interface
	NodeName, NodeUID, ConfigMapUID, SysClassNet string
	Run                                          func(context.Context, string, ...string) ([]byte, error)
	ReadFile                                     func(string) ([]byte, error)
	Physical                                     func(string) bool
	Now                                          func() time.Time
}

func nodeFactsName(uid string) string { return "ani-node-facts-" + contentHash(uid)[:24] }
func newNodeFactsCollector(client dynamic.Interface, nodeName, nodeUID string) *NodeFactsCollector {
	return &NodeFactsCollector{Client: client, NodeName: nodeName, NodeUID: nodeUID, SysClassNet: "/sys/class/net", Now: time.Now, ReadFile: os.ReadFile,
		Run: func(ctx context.Context, name string, args ...string) ([]byte, error) {
			return exec.CommandContext(ctx, name, args...).Output()
		},
		Physical: func(path string) bool { info, err := os.Stat(path); return err == nil && info.IsDir() },
	}
}
func (c *NodeFactsCollector) Collect(ctx context.Context) (NodeFactsDocument, error) {
	doc := NodeFactsDocument{Version: 1, NodeName: c.NodeName, NodeUID: c.NodeUID, CollectedAt: c.Now().UTC()}
	node, err := c.Client.Resource(kcNodes).Get(ctx, c.NodeName, metav1.GetOptions{})
	if err != nil {
		return doc, fmt.Errorf("read node identity")
	}
	if string(node.GetUID()) != c.NodeUID || node.GetDeletionTimestamp() != nil {
		return doc, fmt.Errorf("node identity changed")
	}
	read := func(dst any, command string, args ...string) error {
		body, err := c.Run(ctx, command, args...)
		if err != nil {
			return fmt.Errorf("read %s facts", command)
		}
		if len(body) > 4<<20 {
			return fmt.Errorf("facts exceed bounded response")
		}
		return json.Unmarshal(body, dst)
	}
	var links []struct {
		Name     string   `json:"ifname"`
		Index    int      `json:"ifindex"`
		MTU      int32    `json:"mtu"`
		MAC      string   `json:"address"`
		Flags    []string `json:"flags"`
		Master   any      `json:"master"`
		LinkInfo struct {
			Kind string `json:"info_kind"`
		} `json:"linkinfo"`
	}
	if err = read(&links, "ip", "-j", "-d", "link", "show"); err != nil {
		return doc, err
	}
	var addrs []struct {
		Name string `json:"ifname"`
		Info []struct {
			Local  string `json:"local"`
			Prefix int    `json:"prefixlen"`
		} `json:"addr_info"`
	}
	if err = read(&addrs, "ip", "-j", "address", "show"); err != nil {
		return doc, err
	}
	var routes []struct {
		Dst string `json:"dst"`
		Dev string `json:"dev"`
	}
	if err = read(&routes, "ip", "-j", "route", "show", "table", "all"); err != nil {
		return doc, err
	}
	var routes6 []struct {
		Dst string `json:"dst"`
		Dev string `json:"dev"`
	}
	if err = read(&routes6, "ip", "-6", "-j", "route", "show", "table", "all"); err != nil {
		return doc, err
	}
	routes = append(routes, routes6...)
	var ovs struct {
		Headings []string   `json:"headings"`
		Data     [][]string `json:"data"`
	}
	if err = read(&ovs, "ovs-vsctl", "--timeout=3", "--format=json", "--columns=name", "list", "Interface"); err != nil {
		return doc, err
	}
	if !slices.Equal(ovs.Headings, []string{"name"}) {
		return doc, fmt.Errorf("invalid OVS facts")
	}
	ovsNames := map[string]bool{}
	for _, row := range ovs.Data {
		if len(row) != 1 {
			return doc, fmt.Errorf("invalid OVS row")
		}
		ovsNames[row[0]] = true
	}
	defaultRoute := map[string]bool{}
	for _, r := range routes {
		if r.Dst == "default" || r.Dst == "0.0.0.0/0" || r.Dst == "::/0" {
			defaultRoute[r.Dev] = true
		}
	}
	addresses := map[string][]string{}
	for _, a := range addrs {
		for _, ip := range a.Info {
			prefix, err := netip.ParsePrefix(ip.Local + "/" + strconv.Itoa(ip.Prefix))
			if err != nil {
				return doc, fmt.Errorf("invalid address facts")
			}
			addresses[a.Name] = append(addresses[a.Name], prefix.String())
		}
	}
	names := map[int]string{}
	for _, l := range links {
		names[l.Index] = l.Name
	}
	seen := map[string]bool{}
	for _, l := range links {
		if l.Name == "" || l.Name == "." || l.Name == ".." || strings.ContainsAny(l.Name, "/\\") || seen[l.Name] {
			return doc, fmt.Errorf("invalid link identity")
		}
		seen[l.Name] = true
		kind := l.LinkInfo.Kind
		if kind == "" {
			kind = "virtual"
			if c.Physical(filepath.Join(c.SysClassNet, l.Name, "device")) {
				kind = "device"
			}
		}
		master := ""
		switch v := l.Master.(type) {
		case string:
			master = v
		case float64:
			master = names[int(v)]
			if master == "" {
				master = "unknown"
			}
		case nil:
		default:
			return doc, fmt.Errorf("invalid master facts")
		}
		carrier, err := c.ReadFile(filepath.Join(c.SysClassNet, l.Name, "carrier"))
		carrierOn := err == nil && strings.TrimSpace(string(carrier)) == "1"
		a := addresses[l.Name]
		slices.Sort(a)
		doc.Interfaces = append(doc.Interfaces, biz.NodeInterface{NodeName: c.NodeName, NodeUID: c.NodeUID, Name: l.Name, Kind: kind, MAC: l.MAC, MTU: l.MTU, LinkUp: slices.Contains(l.Flags, "UP"), Carrier: carrierOn, Addresses: a, Master: master, DefaultRoute: defaultRoute[l.Name], Management: defaultRoute[l.Name], OVSManaged: ovsNames[l.Name], ObservedAt: doc.CollectedAt})
	}
	if len(doc.Interfaces) == 0 {
		return doc, fmt.Errorf("node returned no links")
	}
	doc.OVSBridgeMappings, err = c.collectOVSBridgeMappings(ctx)
	if err != nil {
		return doc, err
	}
	doc.OVSBridgeMappingsObserved = true
	for i := range doc.Interfaces {
		iface := &doc.Interfaces[i]
		if iface.Kind != "device" || !iface.OVSManaged {
			continue
		}
		bridge, networks, err := c.collectKCBridgeFacts(ctx, iface.Name)
		if err != nil {
			return doc, err
		}
		iface.KCBridgeNetworks = networks
		iface.KCBridgeReady = len(networks) > 0
		// A default route/address on the internal bridge also uses this device.
		iface.Management = iface.Management || defaultRoute[bridge]
		for _, address := range addresses[bridge] {
			prefix, _ := netip.ParsePrefix(address)
			if !prefix.Addr().IsLinkLocalUnicast() {
				iface.Management = true
			}
		}
	}
	slices.SortFunc(doc.Interfaces, func(a, b biz.NodeInterface) int { return strings.Compare(a.Name, b.Name) })
	return doc, nil
}
func (c *NodeFactsCollector) Publish(ctx context.Context, doc NodeFactsDocument) error {
	if doc.NodeName != c.NodeName || doc.NodeUID != c.NodeUID || doc.CollectedAt.IsZero() {
		return fmt.Errorf("invalid collector identity")
	}
	endpoint := c.Client.Resource(kcConfigMaps).Namespace(kcSystemNamespace)
	cm, err := endpoint.Get(ctx, nodeFactsName(c.NodeUID), metav1.GetOptions{})
	if err != nil {
		return fmt.Errorf("read precreated node facts")
	}
	if cm.GetLabels()[ownerLabel] != factsOwner || cm.GetLabels()[factsNodeLabel] != c.NodeUID || cm.GetUID() == "" || cm.GetResourceVersion() == "" || (c.ConfigMapUID != "" && string(cm.GetUID()) != c.ConfigMapUID) {
		return fmt.Errorf("node facts object identity changed")
	}
	c.ConfigMapUID = string(cm.GetUID())
	body, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	patch, _ := json.Marshal([]map[string]any{{"op": "test", "path": "/metadata/uid", "value": string(cm.GetUID())}, {"op": "test", "path": "/metadata/resourceVersion", "value": cm.GetResourceVersion()}, {"op": "add", "path": "/data", "value": map[string]string{"facts.json": string(body)}}})
	_, err = endpoint.Patch(ctx, cm.GetName(), types.JSONPatchType, patch, metav1.PatchOptions{FieldManager: factsOwner})
	if err != nil {
		return fmt.Errorf("publish node facts")
	}
	return nil
}
func RunNodeFactsCollector(ctx context.Context, logger *slog.Logger) error {
	nodeName, nodeUID := os.Getenv("NETWORK_FACTS_NODE_NAME"), os.Getenv("NETWORK_FACTS_NODE_UID")
	if nodeName == "" || nodeUID == "" {
		return fmt.Errorf("explicit node name and UID are required")
	}
	config, err := rest.InClusterConfig()
	if err != nil {
		return fmt.Errorf("node facts Kubernetes configuration unavailable")
	}
	config.QPS = 2
	config.Burst = 4
	config.Timeout = 5 * time.Second
	config.UserAgent = factsOwner
	client, err := dynamic.NewForConfig(config)
	if err != nil {
		return fmt.Errorf("node facts Kubernetes client unavailable")
	}
	collector := newNodeFactsCollector(client, nodeName, nodeUID)
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	logger.Info("node facts collector started", "node", nodeName)
	for {
		probe, cancel := context.WithTimeout(ctx, 10*time.Second)
		doc, err := collector.Collect(probe)
		if err == nil {
			err = collector.Publish(probe, doc)
		}
		cancel()
		if err != nil && ctx.Err() == nil {
			logger.Error("node facts collection failed", "node", nodeName, "error", err)
		}
		select {
		case <-ctx.Done():
			logger.Info("node facts collector stopped", "node", nodeName)
			return nil
		case <-ticker.C:
		}
	}
}
