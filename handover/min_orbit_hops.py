#!/usr/bin/env python3
import os
import json
import argparse
import random
import re
import collections
from glob import glob

def get_epoch_number(filepath: str) -> int:
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def is_satellite(endpoint: str) -> bool:
    return "sat" in endpoint

def get_link_id(node, sat):
    return (node, sat)

def get_bfs_hops(sat1: str, sat2: str, graph: dict) -> int:
    if sat1 == sat2: return 0
    if sat1 not in graph or sat2 not in graph: return float('inf')
    
    queue = collections.deque([(sat1, 0)])
    visited = {sat1}
    
    while queue:
        curr, hops = queue.popleft()
        for neighbor in graph[curr]:
            if neighbor == sat2: return hops + 1
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, hops + 1))
    return float('inf')

def main():
    parser = argparse.ArgumentParser(description="Apply Minimum Orbit Hops Handover Policy")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--src", type=str, default="usr1")
    parser.add_argument("--dst", type=str, default="grd7")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)
    epoch_files = sorted(glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json")), key=get_epoch_number)
    
    physical_gsl_cache = {}
    visible_sats = {args.src: set(), args.dst: set()}
    active_connections = {args.src: None, args.dst: None}
    
    # 全局ISL追踪结构
    isl_graph = collections.defaultdict(set)

    print(f"Applying Minimum Orbit Hops Policy for {args.src} and {args.dst}...")

    for file_path in epoch_files:
        with open(file_path, 'r') as f: data = json.load(f)

        # 1. Update Physical Graph (Including ISL Graph)
        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            
            # Maintain Global ISL Topology
            if is_satellite(ep1) and is_satellite(ep2):
                if ep2 in isl_graph[ep1]: isl_graph[ep1].remove(ep2)
                if ep1 in isl_graph[ep2]: isl_graph[ep2].remove(ep1)
                
            # Maintain Target node Access Cache
            for node in [args.src, args.dst]:
                if node in (ep1, ep2):
                    sat = ep1 if ep2 == node else ep2
                    if is_satellite(sat):
                        visible_sats[node].discard(sat)
                        physical_gsl_cache.pop(get_link_id(node, sat), None)

        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            
            if is_satellite(ep1) and is_satellite(ep2):
                isl_graph[ep1].add(ep2)
                isl_graph[ep2].add(ep1)
                
            for node in [args.src, args.dst]:
                if node in (ep1, ep2):
                    sat = ep1 if ep2 == node else ep2
                    if is_satellite(sat):
                        visible_sats[node].add(sat)
                        physical_gsl_cache[get_link_id(node, sat)] = link.copy()

        for link in data.get("links-update", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            for node in [args.src, args.dst]:
                if node in (ep1, ep2):
                    sat = ep1 if ep2 == node else ep2
                    if is_satellite(sat):
                        link_id = get_link_id(node, sat)
                        if link_id in physical_gsl_cache: physical_gsl_cache[link_id].update(link)
                        else:
                            physical_gsl_cache[link_id] = link.copy()
                            visible_sats[node].add(sat)

        logical_adds, logical_dels, logical_updates = [], [], []

        # 2. Joint Minimum Orbit Hops Decision
        old_sat_src = active_connections[args.src]
        old_sat_dst = active_connections[args.dst]
        
        src_candidates = sorted(list(visible_sats[args.src]))
        dst_candidates = sorted(list(visible_sats[args.dst]))
        
        new_sat_src = None
        new_sat_dst = None

        if src_candidates and dst_candidates:
            best_metric = float('inf')
            best_pairs = []
            
            # 搜索所有 GSS-USS 组合的最短跳数
            for s_src in src_candidates:
                for s_dst in dst_candidates:
                    hops = get_bfs_hops(s_src, s_dst, isl_graph)
                    if hops < best_metric:
                        best_metric = hops
                        best_pairs = [(s_src, s_dst)]
                    elif hops == best_metric:
                        best_pairs.append((s_src, s_dst))
            
            if best_pairs:
                best_pairs.sort() # Ensure deterministic anti-flapping fallback
                
                # Joint Anti-Flapping: 如果当前对接的卫星对已经是最小跳数，则保持不切换
                if (old_sat_src, old_sat_dst) in best_pairs:
                    new_sat_src, new_sat_dst = old_sat_src, old_sat_dst
                else:
                    # 如果不能两者都保持，尽量保持其中一端不变以减少切换开销
                    stable_src = [p for p in best_pairs if p[0] == old_sat_src]
                    stable_dst = [p for p in best_pairs if p[1] == old_sat_dst]
                    
                    if stable_src:
                        new_sat_src, new_sat_dst = stable_src[0]
                    elif stable_dst:
                        new_sat_src, new_sat_dst = stable_dst[0]
                    else:
                        new_sat_src, new_sat_dst = best_pairs[0]

        # 3. Generate Logical Events (using joint selected sats)
        new_sats = {args.src: new_sat_src, args.dst: new_sat_dst}
        
        for node in [args.src, args.dst]:
            old_sat = active_connections[node]
            new_sat = new_sats[node]
            
            if old_sat != new_sat:
                if old_sat is not None: logical_dels.append({"endpoint1": node, "endpoint2": old_sat, "type": "gsl"})
                if new_sat is not None:
                    cached_link = physical_gsl_cache.get(get_link_id(node, new_sat))
                    if cached_link: logical_adds.append(cached_link.copy())
                active_connections[node] = new_sat
            else:
                if new_sat is not None:
                    for raw_update in data.get("links-update", []):
                        ep1, ep2 = raw_update.get("endpoint1"), raw_update.get("endpoint2")
                        if node in (ep1, ep2) and new_sat in (ep1, ep2):
                            logical_updates.append(raw_update.copy())
                            break

        # 4. Prune and write
        retained_links_add = [l for l in data.get("links-add", []) if args.src not in (l.get("endpoint1"), l.get("endpoint2")) and args.dst not in (l.get("endpoint1"), l.get("endpoint2"))]
        retained_links_add.extend(logical_adds)
        retained_links_del = [l for l in data.get("links-del", []) if args.src not in (l.get("endpoint1"), l.get("endpoint2")) and args.dst not in (l.get("endpoint1"), l.get("endpoint2"))]
        retained_links_del.extend(logical_dels)
        retained_links_update = [l for l in data.get("links-update", []) if args.src not in (l.get("endpoint1"), l.get("endpoint2")) and args.dst not in (l.get("endpoint1"), l.get("endpoint2"))]
        retained_links_update.extend(logical_updates)

        data["links-add"] = retained_links_add
        if retained_links_del or "links-del" in data: data["links-del"] = retained_links_del
        if retained_links_update or "links-update" in data: data["links-update"] = retained_links_update

        with open(os.path.join(args.output_dir, os.path.basename(file_path)), 'w') as f: json.dump(data, f, indent=2)

if __name__ == "__main__": main()