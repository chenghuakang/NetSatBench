#!/usr/bin/env python3
import os
import json
import argparse
import heapq
import re
from glob import glob
from collections import defaultdict

def parse_delay(val):
    if val is None: return float('inf')
    if isinstance(val, (int, float)): return float(val)
    val_str = str(val).lower()
    match = re.search(r'([\d\.]+)', val_str)
    if not match: return float('inf')
    num = float(match.group(1))
    if 'us' in val_str: return num / 1000.0
    elif val_str.endswith('s') and 'ms' not in val_str and 'us' not in val_str: return num * 1000.0
    return num

def get_epoch_number(filepath: str) -> int:
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def is_satellite(endpoint: str) -> bool:
    return "sat" in endpoint

def get_link_id(ep1: str, ep2: str):
    return tuple(sorted([ep1, ep2]))

def is_relevant_link(link, src, dst):
    ep1 = link.get("endpoint1", "")
    ep2 = link.get("endpoint2", "")
    if is_satellite(ep1) and is_satellite(ep2): return True
    if src in (ep1, ep2) or dst in (ep1, ep2): return True
    return False

def dijkstra(active_graph, start, end):
    graph = defaultdict(list)
    for (u, v), w in active_graph.items():
        graph[u].append((w, v))
        graph[v].append((w, u))
    queue = [(0.0, start, [])]
    seen = set()
    min_dist = {start: 0.0}
    while queue:
        cost, node, path = heapq.heappop(queue)
        if node in seen: continue
        seen.add(node)
        path = path + [node]
        if node == end: return cost, path
        for weight, neighbor in graph[node]:
            if neighbor in seen: continue
            old_cost = min_dist.get(neighbor, float('inf'))
            new_cost = cost + weight
            if new_cost < old_cost:
                min_dist[neighbor] = new_cost
                heapq.heappush(queue, (new_cost, neighbor, path))
    return float('inf'), []

def main():
    parser = argparse.ArgumentParser(description="Apply Min-Full-Delay Policy")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save epochs")
    parser.add_argument("--src", type=str, default="usr1", help="First node (default: usr1)")
    parser.add_argument("--dst", type=str, default="grd7", help="Second node (default: grd7)")
    parser.add_argument("--delay-key", type=str, default="delay", help="JSON key for delay")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    epoch_files = glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)

    if not epoch_files:
        print(f"Error: No epoch files found in {args.input_dir}")
        return

    active_graph = {}             # {link_id: delay_val}
    physical_links_cache = {}     # {link_id: link_dict}
    active_connections = {args.src: None, args.dst: None}
    
    handover_counts = {args.src: 0, args.dst: 0}
    epoch_count = 0
    disconnect_count = 0

    raw_stats = {"add": 0, "del": 0, "update": 0}
    routed_stats = {"add": 0, "del": 0, "update": 0}

    print(f"Applying Min-Full-Delay Policy for {args.src} and {args.dst}...")

    for file_path in epoch_files:
        epoch_count += 1
        with open(file_path, 'r') as f:
            data = json.load(f)

        raw_stats["add"] += sum(1 for l in data.get("links-add", []) if is_relevant_link(l, args.src, args.dst))
        raw_stats["del"] += sum(1 for l in data.get("links-del", []) if is_relevant_link(l, args.src, args.dst))
        raw_stats["update"] += sum(1 for l in data.get("links-update", []) if is_relevant_link(l, args.src, args.dst))

        # 1. Update Physical Graph
        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                link_id = get_link_id(ep1, ep2)
                active_graph.pop(link_id, None)
                physical_links_cache.pop(link_id, None)

        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                link_id = get_link_id(ep1, ep2)
                active_graph[link_id] = parse_delay(link.get(args.delay_key, 1.0))
                physical_links_cache[link_id] = link.copy()

        for link in data.get("links-update", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                link_id = get_link_id(ep1, ep2)
                if link_id in physical_links_cache:
                    physical_links_cache[link_id].update(link)
                    active_graph[link_id] = parse_delay(physical_links_cache[link_id].get(args.delay_key, 1.0))
                else:
                    physical_links_cache[link_id] = link.copy()
                    active_graph[link_id] = parse_delay(link.get(args.delay_key, 1.0))

        # 2. Dijkstra Routing
        total_delay, path = dijkstra(active_graph, args.src, args.dst)
        sat_src = path[1] if len(path) > 1 and is_satellite(path[1]) else None
        sat_dst = path[-2] if len(path) > 1 and is_satellite(path[-2]) else None

        logical_adds = []
        logical_dels = []
        logical_updates = []

        # 3. Generate Logical Events
        for node, new_sat in [(args.src, sat_src), (args.dst, sat_dst)]:
            old_sat = active_connections[node]
            if old_sat != new_sat:
                if old_sat is not None:
                    logical_dels.append({"endpoint1": node, "endpoint2": old_sat, "type": "gsl"})
                if new_sat is not None:
                    cached_link = physical_links_cache.get(get_link_id(node, new_sat))
                    if cached_link:
                        logical_adds.append(cached_link.copy())
                active_connections[node] = new_sat
                if old_sat is not None and new_sat is not None:
                    handover_counts[node] += 1
            else:
                if new_sat is not None:
                    for raw_update in data.get("links-update", []):
                        ep1, ep2 = raw_update.get("endpoint1"), raw_update.get("endpoint2")
                        if node in (ep1, ep2) and new_sat in (ep1, ep2):
                            logical_updates.append(raw_update.copy())
                            break

        if not path:
            disconnect_count += 1

        # 4. Prune and write
        retained_links_add = [l for l in data.get("links-add", []) if args.src not in (l.get("endpoint1"), l.get("endpoint2")) and args.dst not in (l.get("endpoint1"), l.get("endpoint2"))]
        retained_links_add.extend(logical_adds)

        retained_links_del = [l for l in data.get("links-del", []) if args.src not in (l.get("endpoint1"), l.get("endpoint2")) and args.dst not in (l.get("endpoint1"), l.get("endpoint2"))]
        retained_links_del.extend(logical_dels)

        retained_links_update = [l for l in data.get("links-update", []) if args.src not in (l.get("endpoint1"), l.get("endpoint2")) and args.dst not in (l.get("endpoint1"), l.get("endpoint2"))]
        retained_links_update.extend(logical_updates)

        routed_stats["add"] += sum(1 for l in retained_links_add if is_relevant_link(l, args.src, args.dst))
        routed_stats["del"] += sum(1 for l in retained_links_del if is_relevant_link(l, args.src, args.dst))
        routed_stats["update"] += sum(1 for l in retained_links_update if is_relevant_link(l, args.src, args.dst))

        data["links-add"] = retained_links_add
        if retained_links_del or "links-del" in data: data["links-del"] = retained_links_del
        if retained_links_update or "links-update" in data: data["links-update"] = retained_links_update

        output_path = os.path.join(args.output_dir, os.path.basename(file_path))
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    print("\n" + "="*70)
    print(" POLICY EXECUTION SUMMARY (FOCUSED ON ISL & TARGET NODES)")
    print("="*70)
    print(f"Output directory : {args.output_dir}")
    print(f"Source Node      : {args.src}")
    print(f"Destination Node : {args.dst}")
    print("-" * 70)
    print(f"Total Epochs Processed : {epoch_count}")
    print(f"Disconnected Epochs    : {disconnect_count}")
    print(f"Handovers Triggered    : {args.src}: {handover_counts[args.src]} | {args.dst}: {handover_counts[args.dst]}")
    print("-" * 70)
    print(f"{'EVENT TYPE':<15} | {'RAW EVENTS':<22} | {'ROUTED EVENTS':<22}")
    print("-" * 70)
    print(f"{'links-add':<15} | {raw_stats['add']:<22} | {routed_stats['add']:<22}")
    print(f"{'links-del':<15} | {raw_stats['del']:<22} | {routed_stats['del']:<22}")
    print(f"{'links-update':<15} | {raw_stats['update']:<22} | {routed_stats['update']:<22}")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()