#!/usr/bin/env python3
import os
import json
import argparse
import heapq
import re
import math
import statistics
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

def compute_metrics_stats(data_list, confidence=0.95):
    """
    计算均值、样本方差及置信区间
    返回 dict: mean, variance, ci_lower, ci_upper, count
    """
    n = len(data_list)
    if n == 0:
        return None
    
    mean = statistics.mean(data_list)
    if n == 1:
        return {
            "count": 1,
            "mean": mean,
            "variance": 0.0,
            "std_dev": 0.0,
            "ci_lower": mean,
            "ci_upper": mean
        }
    
    # 样本方差与标准差 (ddof = 1)
    var = statistics.variance(data_list)
    std_dev = statistics.stdev(data_list)
    standard_error = std_dev / math.sqrt(n)

    # 计算 95% 置信度下的临界值 t*
    t_crit = 1.96  # 默认大样本正态近似
    try:
        from scipy import stats
        t_crit = stats.t.ppf((1 + confidence) / 2.0, df=n - 1)
    except ImportError:
        # 无 scipy 时，小样本查表回退（双尾 alpha=0.05）
        t_table = {
            1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
            6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
            12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042,
            40: 2.021, 60: 2.000, 120: 1.980
        }
        df = n - 1
        if df in t_table:
            t_crit = t_table[df]
        elif df > 120:
            t_crit = 1.960
        else:
            closest_df = min(t_table.keys(), key=lambda k: abs(k - df))
            t_crit = t_table[closest_df]

    margin_of_error = t_crit * standard_error
    return {
        "count": n,
        "mean": mean,
        "variance": var,
        "std_dev": std_dev,
        "ci_lower": mean - margin_of_error,
        "ci_upper": mean + margin_of_error
    }

def main():
    parser = argparse.ArgumentParser(description="Evaluate Network Metrics (Delay & Bandwidth)")
    parser.add_argument("--input-dir", required=True, help="Directory containing epochs")
    parser.add_argument("--src", type=str, default="usr1", help="Source node")
    parser.add_argument("--dst", type=str, default="grd7", help="Destination node")
    parser.add_argument("--delay-key", type=str, default="delay", help="JSON key for delay")
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
    
    # 收集连通状态下的样本值
    delays = []
    bws = []

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
            delays.append(delay)
            bws.append(bw)

    disconnected_epochs = total_epochs - connected_epochs
    connected_ratio = (connected_epochs / total_epochs * 100) if total_epochs > 0 else 0.0

    delay_stats = compute_metrics_stats(delays, confidence=0.95)
    bw_stats = compute_metrics_stats(bws, confidence=0.95)

    print("\n" + "="*56)
    print(f" NETWORK PERFORMANCE REPORT: {args.src} <-> {args.dst}")
    print("="*56)
    print(f"Total Duration (Epochs):       {total_epochs}")
    print(f"Connected Duration (Epochs):   {connected_epochs} ({connected_ratio:.2f}%)")
    print(f"Disconnected Duration:         {disconnected_epochs} Epochs")
    print("-" * 56)
    
    if connected_epochs > 0:
        print("[End-to-End Delay]")
        print(f"  Average (Mean):              {delay_stats['mean']:.3f} ms")
        print(f"  Variance (Sample):           {delay_stats['variance']:.4f} ms²")
        print(f"  95% Confidence Interval:     [{delay_stats['ci_lower']:.3f}, {delay_stats['ci_upper']:.3f}] ms")
        print()
        print("[Bottleneck Bandwidth]")
        print(f"  Average (Mean):              {bw_stats['mean']:.2f} Mbps")
        print(f"  Variance (Sample):           {bw_stats['variance']:.4f} (Mbps)²")
        print(f"  95% Confidence Interval:     [{bw_stats['ci_lower']:.2f}, {bw_stats['ci_upper']:.2f}] Mbps")
    else:
        print("Network was completely disconnected for the entire simulation.")
    print("="*56 + "\n")

if __name__ == "__main__":
    main()