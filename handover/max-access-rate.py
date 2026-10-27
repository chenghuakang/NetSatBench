#!/usr/bin/env python3
import os
import json
import argparse
import re
from glob import glob

def parse_bw(val):
    """解析带宽/速率字符串，统一转换为 Mbps"""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).lower()
    match = re.search(r'([\d\.]+)', val_str)
    if not match:
        return 0.0
    num = float(match.group(1))
    
    if 'g' in val_str:
        return num * 1000.0
    elif 'k' in val_str:
        return num / 1000.0
    elif 'm' in val_str:
        return num
    return num

def get_epoch_number(filepath: str) -> int:
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def is_satellite(endpoint: str) -> bool:
    return "sat" in endpoint

def get_link_id(node, sat):
    return (node, sat)

def is_relevant_link(link, src, dst):
    """
    判断该链路是否是我们要纳入统计规模的链路：
    1. 纯星间链路 (ISL) -> 纳入统计
    2. 包含 src (如 usr1) 或 dst (如 grd7) 的链路 -> 纳入统计
    3. 其他无关节点 (如 usr2, grd3) -> 忽略，不计入规模
    """
    ep1 = link.get("endpoint1", "")
    ep2 = link.get("endpoint2", "")
    
    # 1. 骨干星间网络
    if is_satellite(ep1) and is_satellite(ep2):
        return True
    
    # 2. 我们关注的优化节点
    if src in (ep1, ep2) or dst in (ep1, ep2):
        return True
        
    return False

def main():
    parser = argparse.ArgumentParser(description="Apply Max-Access-Rate Policy with focused event statistics")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save epochs")
    parser.add_argument("--src", type=str, default="usr1", help="First node (default: usr1)")
    parser.add_argument("--dst", type=str, default="grd7", help="Second node (default: grd7)")
    parser.add_argument("--bw-key", type=str, default="rate", help="JSON key representing access rate")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    epoch_files = glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)

    if not epoch_files:
        print(f"Error: No epoch files found in {args.input_dir}")
        return

    # 物理状态跟踪
    physical_gsl_cache = {}  
    visible_sats = {args.src: set(), args.dst: set()}
    active_connections = {args.src: None, args.dst: None}
    
    handover_counts = {args.src: 0, args.dst: 0}
    epoch_count = 0
    disconnect_count = 0

    # 全局事件统计计数器 (仅针对目标网络和节点)
    raw_stats = {"add": 0, "del": 0, "update": 0}
    routed_stats = {"add": 0, "del": 0, "update": 0}

    print(f"Applying Max-Access-Rate Policy for {args.src} and {args.dst}...")

    for file_path in epoch_files:
        epoch_count += 1
        with open(file_path, 'r') as f:
            data = json.load(f)

        # === 统计原始事件数量（过滤无关节点） ===
        raw_stats["add"] += sum(1 for l in data.get("links-add", []) if is_relevant_link(l, args.src, args.dst))
        raw_stats["del"] += sum(1 for l in data.get("links-del", []) if is_relevant_link(l, args.src, args.dst))
        raw_stats["update"] += sum(1 for l in data.get("links-update", []) if is_relevant_link(l, args.src, args.dst))

        # 1. 维护底层的物理增量状态
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
                        if link_id in physical_gsl_cache:
                            physical_gsl_cache[link_id].update(link)
                        else:
                            physical_gsl_cache[link_id] = link.copy()
                            visible_sats[node].add(sat)

        logical_adds = []
        logical_dels = []
        logical_updates = []

        # 2. 策略计算
        for node in [args.src, args.dst]:
            best_sat = None
            max_rate = -1.0

            for sat in visible_sats[node]:
                link = physical_gsl_cache.get(get_link_id(node, sat))
                if link:
                    r = parse_bw(link.get(args.bw_key))
                    if r > max_rate:
                        max_rate = r
                        best_sat = sat

            old_sat = active_connections[node]
            new_sat = best_sat

            # 消除抖动
            if old_sat in visible_sats[node]:
                old_link = physical_gsl_cache.get(get_link_id(node, old_sat))
                if old_link:
                    old_rate = parse_bw(old_link.get(args.bw_key))
                    if old_rate >= max_rate:
                        new_sat = old_sat

            # 3. 生成逻辑事件
            if old_sat != new_sat:
                if old_sat is not None:
                    logical_dels.append({"endpoint1": node, "endpoint2": old_sat, "type": "gsl"})
                if new_sat is not None:
                    cached_link = physical_gsl_cache.get(get_link_id(node, new_sat))
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

        if active_connections[args.src] is None or active_connections[args.dst] is None:
            disconnect_count += 1

        # 4. 过滤并重写目标文件
        retained_links_add = []
        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if args.src not in (ep1, ep2) and args.dst not in (ep1, ep2):
                retained_links_add.append(link)
        retained_links_add.extend(logical_adds)

        retained_links_del = []
        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if args.src not in (ep1, ep2) and args.dst not in (ep1, ep2):
                retained_links_del.append(link)
        retained_links_del.extend(logical_dels)

        retained_links_update = []
        for link in data.get("links-update", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if args.src not in (ep1, ep2) and args.dst not in (ep1, ep2):
                retained_links_update.append(link)
        retained_links_update.extend(logical_updates)

        # === 统计剪枝后事件数量（过滤无关节点） ===
        routed_stats["add"] += sum(1 for l in retained_links_add if is_relevant_link(l, args.src, args.dst))
        routed_stats["del"] += sum(1 for l in retained_links_del if is_relevant_link(l, args.src, args.dst))
        routed_stats["update"] += sum(1 for l in retained_links_update if is_relevant_link(l, args.src, args.dst))

        # 写入文件
        data["links-add"] = retained_links_add
        if retained_links_del or "links-del" in data:
            data["links-del"] = retained_links_del
        if retained_links_update or "links-update" in data:
            data["links-update"] = retained_links_update

        output_path = os.path.join(args.output_dir, os.path.basename(file_path))
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    # === 打印统计信息 ===
    print("\n" + "="*70)
    print(" 📊 POLICY EXECUTION SUMMARY (FOCUSED ON ISL & TARGET NODES)")
    print("="*70)
    print(f"Output directory : {args.output_dir}")
    print(f"Source Node      : {args.src}")
    print(f"Destination Node : {args.dst}")
    print("-" * 70)
    print(f"Total Epochs Processed : {epoch_count}")
    print(f"Disconnected Epochs    : {disconnect_count}")
    print(f"Handovers Triggered    : {args.src}: {handover_counts[args.src]} | {args.dst}: {handover_counts[args.dst]}")
    print("-" * 70)
    print(f"{'EVENT TYPE':<15} | {'RAW EVENTS (Focused)':<22} | {'ROUTED EVENTS (Focused)':<22}")
    print("-" * 70)
    print(f"{'links-add':<15} | {raw_stats['add']:<22} | {routed_stats['add']:<22}")
    print(f"{'links-del':<15} | {raw_stats['del']:<22} | {routed_stats['del']:<22}")
    print(f"{'links-update':<15} | {raw_stats['update']:<22} | {routed_stats['update']:<22}")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()