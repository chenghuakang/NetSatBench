#!/usr/bin/env python3
import os
import json
import argparse
import heapq
import re
from glob import glob
from collections import defaultdict

def parse_delay(val):
    """解析延迟字符串，统一转换为 ms"""
    if val is None:
        return 1.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).lower()
    match = re.search(r'([\d\.]+)', val_str)
    if not match:
        return 1.0
    num = float(match.group(1))
    
    if 'us' in val_str:
        return num / 1000.0
    elif val_str.endswith('s') and 'ms' not in val_str and 'us' not in val_str:
        return num * 1000.0  # 秒转毫秒
    return num

def parse_bw(val):
    """解析带宽字符串，统一转换为 Mbps"""
    if val is None:
        return 100.0  # 兜底默认值
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).lower()
    match = re.search(r'([\d\.]+)', val_str)
    if not match:
        return 100.0
    num = float(match.group(1))
    
    # 增强单位解析，支持 mbit, gbit, kbit, mbps 等
    if 'g' in val_str:       # Gbps, Gbit
        return num * 1000.0
    elif 'k' in val_str:     # Kbps, Kbit
        return num / 1000.0
    elif 'm' in val_str:     # Mbps, Mbit
        return num
    return num               # 默认视作 Mbps

def get_epoch_number(filepath: str) -> int:
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def get_link_id(ep1: str, ep2: str):
    return tuple(sorted([ep1, ep2]))

def dijkstra_eval(active_graph, start, end):
    """计算最小延迟路径，并返回路径上的瓶颈带宽"""
    graph = defaultdict(dict)
    for (u, v), props in active_graph.items():
        graph[u][v] = props
        graph[v][u] = props

    queue = [(0.0, start, [start])]
    seen = set()
    min_dist = {start: 0.0}

    while queue:
        cost, node, path = heapq.heappop(queue)
        
        # 找到目标节点，计算这条路径上的瓶颈带宽
        if node == end:
            bottleneck_bw = float('inf')
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                bw = graph[u][v].get('bw', float('inf'))
                if bw < bottleneck_bw:
                    bottleneck_bw = bw
            return cost, bottleneck_bw, path
            
        if node in seen:
            continue
        seen.add(node)

        for neighbor, props in graph[node].items():
            if neighbor in seen:
                continue
            weight = props.get('delay', 1.0)
            new_cost = cost + weight
            if new_cost < min_dist.get(neighbor, float('inf')):
                min_dist[neighbor] = new_cost
                heapq.heappush(queue, (new_cost, neighbor, path + [neighbor]))
    
    return float('inf'), 0.0, []

def main():
    parser = argparse.ArgumentParser(description="Evaluate Network Metrics (Delay & Bandwidth)")
    parser.add_argument("--input-dir", required=True, help="Directory containing epochs")
    parser.add_argument("--src", type=str, default="usr1", help="Source node")
    parser.add_argument("--dst", type=str, default="grd7", help="Destination node")
    parser.add_argument("--delay-key", type=str, default="delay", help="JSON key for delay")
    # 将这里的默认值改成了 "rate"
    parser.add_argument("--bw-key", type=str, default="rate", help="JSON key for bandwidth/rate") 
    args = parser.parse_args()

    epoch_files = glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)

    if not epoch_files:
        print(f"Error: No epoch files found in {args.input_dir}")
        return

    active_graph = {}
    
    total_epochs = 0
    connected_epochs = 0
    
    sum_delay = 0.0
    sum_bw = 0.0

    print(f"Evaluating metrics for {args.src} -> {args.dst}...")
    
    for file_path in epoch_files:
        total_epochs += 1
        with open(file_path, 'r') as f:
            data = json.load(f)

        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                link_id = get_link_id(ep1, ep2)
                active_graph.pop(link_id, None)

        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                link_id = get_link_id(ep1, ep2)
                delay_val = parse_delay(link.get(args.delay_key, 1.0))
                bw_val = parse_bw(link.get(args.bw_key, 100.0))
                active_graph[link_id] = {'delay': delay_val, 'bw': bw_val}

        delay, bw, path = dijkstra_eval(active_graph, args.src, args.dst)

        if path:
            connected_epochs += 1
            sum_delay += delay
            sum_bw += bw

    disconnected_epochs = total_epochs - connected_epochs
    connected_ratio = (connected_epochs / total_epochs * 100) if total_epochs > 0 else 0.0
    
    avg_delay = (sum_delay / connected_epochs) if connected_epochs > 0 else 0.0
    avg_bw = (sum_bw / connected_epochs) if connected_epochs > 0 else 0.0

    print("\n" + "="*50)
    print(f" NETWORK PERFORMANCE REPORT: {args.src} <-> {args.dst}")
    print("="*50)
    print(f"Total Duration (Epochs):      {total_epochs}")
    print(f"Connected Duration (Epochs):  {connected_epochs} ({connected_ratio:.2f}%)")
    print(f"Disconnected Duration:        {disconnected_epochs} Epochs")
    print("-" * 50)
    if connected_epochs > 0:
        print(f"Average End-to-End Delay:     {avg_delay:.3f} ms")
        print(f"Average Bottleneck Bandwidth: {avg_bw:.2f} Mbps")
    else:
        print("Network was completely disconnected for the entire simulation.")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()