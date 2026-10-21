package data

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
)

// collectKCBridge proves that the interface belongs to kc's actual bridge and
// local-network mapping. KC's node annotation alone cannot prove OVS wiring.
func (c *NodeFactsCollector) collectKCBridge(ctx context.Context, device string) (string, bool, error) {
	bridge, networks, err := c.collectKCBridgeFacts(ctx, device)
	return bridge, slices.Contains(networks, "net."+device), err
}

// Keep the exact local-network names: a valid mapping belonging to another
// vCluster cannot prove that this switch has been wired by the daemon.
func (c *NodeFactsCollector) collectKCBridgeFacts(ctx context.Context, device string) (string, []string, error) {
	run := func(args ...string) ([]byte, error) {
		body, err := c.Run(ctx, "ovs-vsctl", append([]string{"--timeout=3"}, args...)...)
		if err != nil || len(body) > 4<<20 {
			return nil, fmt.Errorf("read bounded OVS bridge facts")
		}
		return body, nil
	}
	body, err := run("iface-to-br", device)
	if err != nil {
		return "", nil, err
	}
	bridge := strings.TrimSpace(string(body))
	if bridge != "br-"+device {
		return bridge, nil, nil
	}
	readMap := func(table string, record string) (map[string]string, error) {
		body, err := run("--format=json", "--columns=external_ids", "list", table, record)
		if err != nil {
			return nil, err
		}
		return decodeOVSExternalIDs(body)
	}
	ids, err := readMap("Bridge", bridge)
	if err != nil {
		return bridge, nil, err
	}
	if ids["vendor"] != "kc-networking" || ids["purpose"] != "vlan-network" {
		return bridge, nil, nil
	}
	global, err := readMap("Open_vSwitch", ".")
	if err != nil {
		return bridge, nil, err
	}
	counts := map[string]int{}
	invalid := map[string]bool{}
	for _, mapping := range strings.Split(global["ovn-bridge-mappings"], ",") {
		network, target, ok := strings.Cut(strings.TrimSpace(mapping), ":")
		if !strings.HasSuffix(network, "."+device) {
			continue
		}
		counts[network]++
		if !ok || target != bridge {
			invalid[network] = true
		}
	}
	networks := []string{}
	for network, count := range counts {
		if count == 1 && !invalid[network] {
			networks = append(networks, network)
		}
	}
	slices.Sort(networks)
	return bridge, networks, nil
}

func decodeOVSExternalIDs(body []byte) (map[string]string, error) {
	var result struct {
		Headings []string `json:"headings"`
		Data     [][]any  `json:"data"`
	}
	if json.Unmarshal(body, &result) != nil || !slices.Equal(result.Headings, []string{"external_ids"}) || len(result.Data) != 1 || len(result.Data[0]) != 1 {
		return nil, fmt.Errorf("invalid OVS external IDs")
	}
	value, ok := result.Data[0][0].([]any)
	if !ok || len(value) != 2 || value[0] != "map" {
		return nil, fmt.Errorf("invalid OVS external IDs map")
	}
	entries, ok := value[1].([]any)
	if !ok {
		return nil, fmt.Errorf("invalid OVS external IDs entries")
	}
	values := map[string]string{}
	for _, entry := range entries {
		pair, ok := entry.([]any)
		if !ok || len(pair) != 2 {
			return nil, fmt.Errorf("invalid OVS external IDs pair")
		}
		key, keyOK := pair[0].(string)
		v, valueOK := pair[1].(string)
		_, duplicate := values[key]
		if !keyOK || !valueOK || duplicate {
			return nil, fmt.Errorf("invalid OVS external IDs value")
		}
		values[key] = v
	}
	return values, nil
}

// Global mappings are collected even when a physical port has already left
// OVS. Only an explicit successful empty collection proves mapping removal.
func (c *NodeFactsCollector) collectOVSBridgeMappings(ctx context.Context) (map[string]string, error) {
	body, err := c.Run(ctx, "ovs-vsctl", "--timeout=3", "--format=json", "--columns=external_ids", "list", "Open_vSwitch", ".")
	if err != nil || len(body) > 4<<20 {
		return nil, fmt.Errorf("read bounded OVS mappings")
	}
	ids, err := decodeOVSExternalIDs(body)
	if err != nil {
		return nil, err
	}
	mappings := map[string]string{}
	for _, raw := range strings.Split(ids["ovn-bridge-mappings"], ",") {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		network, bridge, ok := strings.Cut(raw, ":")
		_, duplicate := mappings[network]
		if !ok || network == "" || bridge == "" || strings.Contains(bridge, ":") || duplicate {
			return nil, fmt.Errorf("ambiguous OVS bridge mapping")
		}
		mappings[network] = bridge
	}
	return mappings, nil
}
