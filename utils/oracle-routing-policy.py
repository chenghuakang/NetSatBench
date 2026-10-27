#!/usr/bin/env python3
"""
oracle-routing-policy.py
"""

import json
import os
import re
import argparse
import heapq
from glob import glob
import logging

import etcd3

logging.basicConfig(level="INFO", format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ==========================================
# ATTRIBUTE PARSERS
# ==========================================
def parse_delay(value) -> float:
    if not value: return 0.0
    match = re.match(r"([0-9\.]+)([a-zA-Z]+)?", str(value).strip())
    if not match: return 0.0
    num = float(match.group(1))
    unit = match.group(2)
    units = {'s': 1000.0, 'ms': 1.0, 'us': 0.001, 'ns': 0.000001}
    return num * units.get(unit, 1.0)

def parse_rate(value) -> float:
    if not value: return 0.0
    match = re.search(r"([0-9\.]+)", str(value))
    return float(match.group(1)) if match else 0.0

def parse_lifetime(value) -> float:
    if value is None or str(value).strip().lower() == "null":
        return float('inf')
    return float(value)

def extract_link_attrs(link) -> dict:
    return {
        'delay': parse_delay(link.get('delay', 0)),
        'rate': parse_rate(link.get('rate', 0)),
        'lifetime': parse_lifetime(link.get('expected_duration')),
        'hops': 1.0
    }

# ==========================================
# CUSTOM WIDEST/SHORTEST PATH ALGORITHM
# ==========================================
def dijkstra_custom(adj, num_nodes, src_idx, metric):
    if metric == "hops":
        init_d = float('inf')
        start_d = 0.0
        def get_new_dist(d_u, attrs): return d_u + attrs['hops']
        def make_heap_item(d, v): return (d, v)
        def is_better(new_d, old_d): return new_d < old_d
        
    elif metric == "delay":
        init_d = float('inf')
        start_d = 0.0
        def get_new_dist(d_u, attrs): return d_u + attrs['delay']
        def make_heap_item(d, v): return (d, v)
        def is_better(new_d, old_d): return new_d < old_d
        
    elif metric == "max-min-rate":
        init_d = (-float('inf'), -float('inf'))
        start_d = (float('inf'), 0.0)
        def get_new_dist(d_u, attrs): 
            return (min(d_u[0], attrs['rate']), d_u[1] - attrs['hops'])
        def make_heap_item(d, v): return (-d[0], -d[1], v)
        def is_better(new_d, old_d): return new_d > old_d
            
    elif metric in ["lifetime", "max-min-visibility"]:
        init_d = (-float('inf'), -float('inf'))
        start_d = (float('inf'), 0.0)
        def get_new_dist(d_u, attrs): 
            return (min(d_u[0], attrs['lifetime']), d_u[1] - attrs['hops'])
        def make_heap_item(d, v): return (-d[0], -d[1], v)
        def is_better(new_d, old_d): return new_d > old_d
    else:
        raise ValueError(f"Unknown metric {metric}")

    dist = {i: init_d for i in range(num_nodes)}
    pred = {i: None for i in range(num_nodes)}
    
    dist[src_idx] = start_d
    pq = [make_heap_item(start_d, src_idx)]
    
    while pq:
        item = heapq.heappop(pq)
        u = item[-1]
        
        if metric in ["hops", "delay"]:
            d_u = item[0]
        else:
            d_u = (-item[0], -item[1])
            
        if is_better(dist[u], d_u):
            continue
            
        for v, attrs in adj[u].items():
            new_dist = get_new_dist(d_u, attrs)
            if is_better(new_dist, dist[v]):
                dist[v] = new_dist
                pred[v] = u
                heapq.heappush(pq, make_heap_item(new_dist, v))
                
    return dist, pred

def get_next_hop(pred, src, target):
    curr = target
    if pred[curr] is None: return None
    while pred[curr] != src:
        curr = pred[curr]
        if curr is None: return None
    return curr

# ==========================================
# MAIN ROUTING LOGIC
# ==========================================
def compute_routes(etcd_client, args):
    log.info("📁 Loading Etcd mappings...")
    nodes = {meta.key.decode().split('/')[-1]: json.loads(val.decode()) 
             for val, meta in etcd_client.get_prefix("/config/nodes")}
    
    # 恢复官方原版的 IP 获取逻辑：稳定可靠
    ip_map = {meta.key.decode().split('/')[-1]: val.decode().strip() 
              for val, meta in etcd_client.get_prefix("/config/etchosts/")}

    if not ip_map:
        log.error("❌ Etcd 返回的 IP 列表为空！请确保已经成功执行了 nsb.py deploy。")
        return

    node_map = {name: idx for idx, name in enumerate(nodes.keys())}
    inv_node_map = {idx: name for name, idx in node_map.items()}
    num_nodes = len(node_map)

    # Initialize Graph
    adj = {i: {} for i in range(num_nodes)}
    
    epoch_files = sorted(glob(os.path.join(args.epoch_dir, args.file_pattern)), 
                         key=lambda x: int(re.findall(r"(\d+)", os.path.basename(x))[-1]))
    
    log.info(f"🔎 Found {len(epoch_files)} epoch files.")
    log.info(f"⚙️ Routing Metric: {args.routing_metrics}")
    
    if not os.path.exists(args.out_epoch_dir):
        os.makedirs(args.out_epoch_dir)

    for file_idx, path in enumerate(epoch_files):
        with open(path, "r") as f:
            epoch_data = json.load(f)
            
        for link in epoch_data.get("links-del", []):
            u, v = node_map.get(link["endpoint1"]), node_map.get(link["endpoint2"])
            if u is not None and v is not None:
                adj[u].pop(v, None)
                adj[v].pop(u, None)
                
        for link in epoch_data.get("links-add", []) + epoch_data.get("links-update", []):
            u_name = link["endpoint1"]
            v_name = link["endpoint2"]
            u, v = node_map.get(u_name), node_map.get(v_name)
            
            if u is not None and v is not None and u != v:
                attrs = extract_link_attrs(link)
                
                if attrs['lifetime'] < args.min_lifetime_threshold:
                    adj[u].pop(v, None)
                    adj[v].pop(u, None)
                    continue
                
                # --- CROSS TYPE PENALTY (强制防绕路下地) ---
                type_u = nodes[u_name].get("type", "unknown")
                type_v = nodes[v_name].get("type", "unknown")
                if type_u != type_v:
                    attrs['hops'] += 1000.0  # 星地链路强加 1000 跳
                
                adj[u][v] = attrs
                adj[v][u] = attrs

        # 3. Compute Routes
        route_commands = {}
        for src_name, src_idx in node_map.items():
            # 绝对不能跳过 user 节点，必须给 user 算第一跳！
            dist, pred = dijkstra_custom(adj, num_nodes, src_idx, args.routing_metrics)
            
            for dst_name, dst_idx in node_map.items():
                if src_idx == dst_idx: continue
                
                nh_idx = get_next_hop(pred, src_idx, dst_idx)
                if nh_idx is not None:
                    nh_name = inv_node_map[nh_idx]
                    dst_ip = ip_map.get(dst_name)
                    nh_ip = ip_map.get(nh_name)
                    
                    if dst_ip and nh_ip:
                        cmd = f"ip route replace {dst_ip} via {nh_ip} dev vl_{nh_name}_1 metric 100 onlink"
                        route_commands.setdefault(src_name, []).append(cmd)

        # 4. Assemble New Epoch
        new_epoch_data = {
            "time": epoch_data.get("time"),
            "run": {src: ["sleep 2; " + "; ".join(cmds)] for src, cmds in route_commands.items() if cmds},
            "links-add": epoch_data.get("links-add", []),
            "links-del": epoch_data.get("links-del", []),
            "links-update": epoch_data.get("links-update", [])
        }

        out_path = os.path.join(args.out_epoch_dir, os.path.basename(path))
        with open(out_path, "w") as f_out:
            json.dump(new_epoch_data, f_out, indent=2)

        if (file_idx + 1) % 10 == 0:
            log.info(f"   ... Processed {file_idx + 1}/{len(epoch_files)} epochs")

    log.info("👍 Route computation completed!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--etcd-host", default="127.0.0.1")
    parser.add_argument("--etcd-port", type=int, default=2379)
    parser.add_argument("--epoch-dir", required=True)
    parser.add_argument("--file-pattern", default="NetSatBench-epoch*.json")
    parser.add_argument("--out-epoch-dir", required=True)
    parser.add_argument("--routing-metrics", 
                        choices=["hops", "delay", "max-min-rate", "lifetime", "max-min-visibility"], 
                        default="max-min-visibility")
    parser.add_argument("--min-lifetime-threshold", type=float, default=0.0)
    args = parser.parse_args()

    etcd = etcd3.client(host=args.etcd_host, port=args.etcd_port)
    compute_routes(etcd, args)