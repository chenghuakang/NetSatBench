#!/usr/bin/env python3
import os
import json
import argparse
import random
import re
from glob import glob

def get_epoch_number(filepath: str) -> int:
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def is_satellite(endpoint: str) -> bool:
    return "sat" in endpoint

def get_link_id(node, sat):
    return (node, sat)

def is_relevant_link(link, src, dst):
    ep1 = link.get("endpoint1", "")
    ep2 = link.get("endpoint2", "")
    if is_satellite(ep1) and is_satellite(ep2): return True
    if src in (ep1, ep2) or dst in (ep1, ep2): return True
    return False

def main():
    parser = argparse.ArgumentParser(description="Apply Max-Min Visibility Handover Policy")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save epochs")
    parser.add_argument("--src", type=str, default="usr1", help="First node (default: usr1)")
    parser.add_argument("--dst", type=str, default="grd7", help="Second node (default: grd7)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    random.seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)
    
    epoch_files = glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)

    if not epoch_files:
        return

    physical_gsl_cache = {}
    visible_sats = {args.src: set(), args.dst: set()}
    active_connections = {args.src: None, args.dst: None}
    handover_counts = {args.src: 0, args.dst: 0}

    print(f"Applying Max-Min Visibility Policy for {args.src} and {args.dst}...")

    for file_path in epoch_files:
        with open(file_path, 'r') as f: data = json.load(f)

        # 1. Update Physical Graph
        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            for node in [args.src, args.dst]:
                if node in (ep1, ep2):
                    sat = ep1 if ep2 == node else ep2
                    if is_satellite(sat):
                        visible_sats[node].discard(sat)
                        physical_gsl_cache.pop(get_link_id(node, sat), None)

        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
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

        # 2. Max-Min Visibility Routing Decision
        for node in [args.src, args.dst]:
            old_sat = active_connections[node]
            current_visible = sorted(list(visible_sats[node]))
            new_sat = old_sat
            
            if not current_visible:
                new_sat = None
            else:
                best_sat = None
                best_metric = -1.0
                for sat in current_visible:
                    duration = float(physical_gsl_cache.get(get_link_id(node, sat), {}).get("expected_duration", 0.0))
                    if duration > best_metric:
                        best_metric = duration
                        best_sat = sat
                
                # Anti-flapping
                if old_sat in visible_sats[node]:
                    old_duration = float(physical_gsl_cache.get(get_link_id(node, old_sat), {}).get("expected_duration", 0.0))
                    if old_duration >= best_metric: best_sat = old_sat
                new_sat = best_sat

        # 3. Generate Logical Events
            if old_sat != new_sat:
                if old_sat is not None: logical_dels.append({"endpoint1": node, "endpoint2": old_sat, "type": "gsl"})
                if new_sat is not None:
                    cached_link = physical_gsl_cache.get(get_link_id(node, new_sat))
                    if cached_link: logical_adds.append(cached_link.copy())
                active_connections[node] = new_sat
                if old_sat is not None and new_sat is not None: handover_counts[node] += 1
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

        with open(os.path.join(args.output_dir, os.path.basename(file_path)), 'w') as f:
            json.dump(data, f, indent=2)
            
if __name__ == "__main__": main()