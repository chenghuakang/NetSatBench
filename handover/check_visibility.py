#!/usr/bin/env python3
import os
import json
import argparse
import re
from glob import glob

def is_satellite(endpoint: str) -> bool:
    return "sat" in endpoint

def get_link_id(ep1: str, ep2: str):
    """将两端节点排序后生成唯一标识，防止方向不同导致匹配失败"""
    return tuple(sorted([ep1, ep2]))

def get_epoch_number(filepath: str) -> int:
    """从文件名中提取 epoch 数字用于正确排序"""
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def main():
    parser = argparse.ArgumentParser(description="Check true available satellites by tracking state")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--node1", type=str, default="usr1", help="First node to check (default: usr1)")
    parser.add_argument("--node2", type=str, default="grd7", help="Second node to check (default: grd7)")
    args = parser.parse_args()

    # 获取并按真正的数字顺序排序文件
    epoch_files = glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)

    if not epoch_files:
        print(f"❌ No epoch files found in {args.input_dir}")
        return

    print(f"📊 Tracking active satellites for '{args.node1}' and '{args.node2}'...\n")
    print(f"{'Epoch File':<35} | {args.node1 + ' Active':<15} | {args.node2 + ' Active':<15}")
    print("-" * 72)

    # 维护当前网络中所有存活的连接
    active_links = set()

    node1_stats = []
    node2_stats = []

    for file_path in epoch_files:
        with open(file_path, 'r') as f:
            data = json.load(f)

        # 1. 处理新增的链路
        for link in data.get("links-add", []):
            ep1 = link.get("endpoint1", "")
            ep2 = link.get("endpoint2", "")
            active_links.add(get_link_id(ep1, ep2))

        # 2. 处理断开的链路 (假设 JSON 中有 links-del 字段)
        # 如果你的数据中用其他方式表示断开，请告诉我！
        for link in data.get("links-del", []):
            ep1 = link.get("endpoint1", "")
            ep2 = link.get("endpoint2", "")
            active_links.discard(get_link_id(ep1, ep2))

        # 3. 在当前活动的链路池中统计 node1 和 node2 的卫星数量
        node1_count = 0
        node2_count = 0
        for ep1, ep2 in active_links:
            if args.node1 in (ep1, ep2):
                other = ep2 if ep1 == args.node1 else ep1
                if is_satellite(other):
                    node1_count += 1
            if args.node2 in (ep1, ep2):
                other = ep2 if ep1 == args.node2 else ep1
                if is_satellite(other):
                    node2_count += 1
        
        filename = os.path.basename(file_path)
        # 仅在调试时打印所有内容，或者你可以去掉这行的注释来查看每一行
        # print(f"{filename:<35} | {node1_count:<15} | {node2_count:<15}")

        node1_stats.append(node1_count)
        node2_stats.append(node2_count)

    # 打印最终统计摘要
    print("-" * 72)
    print("📈 State-Tracked Summary Statistics:")
    print(f"Total Epochs Processed: {len(epoch_files)}")
    
    if node1_stats:
        avg_1 = sum(node1_stats) / len(node1_stats)
        zero_count_1 = node1_stats.count(0)
        print(f"[{args.node1}] Avg: {avg_1:.2f} | Min: {min(node1_stats)} | Max: {max(node1_stats)} | Disconnected Epochs: {zero_count_1}")
        
    if node2_stats:
        avg_2 = sum(node2_stats) / len(node2_stats)
        zero_count_2 = node2_stats.count(0)
        print(f"[{args.node2}] Avg: {avg_2:.2f} | Min: {min(node2_stats)} | Max: {max(node2_stats)} | Disconnected Epochs: {zero_count_2}")

if __name__ == "__main__":
    main()