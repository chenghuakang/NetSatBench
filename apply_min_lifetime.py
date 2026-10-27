#!/usr/bin/env python3
import os
import json
import argparse
from glob import glob

def parse_delay(delay_str):
    """将 '7.0ms' 格式的字符串解析为浮点数"""
    if not delay_str:
        return float('inf')
    return float(delay_str.replace("ms", "").strip())

def main():
    parser = argparse.ArgumentParser(description="Apply Minimum Lifetime (Threshold) Filter to Epochs")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save filtered epochs")
    # 添加最小存活时间阈值参数，默认 120 秒
    parser.add_argument("--min-duration", type=float, default=120.0, 
                        help="Minimum expected duration threshold in seconds (default: 120.0)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    epoch_files = sorted(glob(os.path.join(args.input_dir, "*.json")))

    if not epoch_files:
        print(f"No epoch files found in {args.input_dir}")
        return

    for file_path in epoch_files:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        if "links-add" not in data:
            with open(os.path.join(args.output_dir, os.path.basename(file_path)), 'w') as f:
                json.dump(data, f, indent=2)
            continue

        node_candidates = {}
        links = data["links-add"]
        
        for link in links:
            src, dst = link["endpoint1"], link["endpoint2"]
            
            # 处理 duration，防空值 (null -> None)
            raw_duration = link.get("expected_duration")
            if raw_duration is None:
                raw_duration = link.get("duration", 0)
            duration = float(raw_duration) if raw_duration is not None else 0.0
            
            # 处理 delay，用于满足阈值时的优选
            delay = parse_delay(link.get("delay", "0ms"))
            
            for node in [src, dst]:
                if "usr" in node or "grd" in node:
                    if node not in node_candidates:
                        node_candidates[node] = []
                    node_candidates[node].append({
                        "duration": duration,
                        "delay": delay,
                        "link": link
                    })

        retained_links = []
        for link in links:
            # 星间链路 (ISL) 直接保留
            if "sat" in link["endpoint1"] and "sat" in link["endpoint2"]:
                retained_links.append(link)

        for node, candidates in node_candidates.items():
            if not candidates:
                continue
            
            # 策略：筛选出满足 Minimum Lifetime 阈值的候选链路
            valid_candidates = [c for c in candidates if c["duration"] >= args.min_duration]
            
            if valid_candidates:
                # 如果有满足阈值的链路，选择延迟 (delay) 最小的 (Nearest-Neighbor 思想)
                valid_candidates.sort(key=lambda x: x["delay"])
                best_link = valid_candidates[0]["link"]
            else:
                # 降级容错：如果没有满足阈值的链路，回退到 Max-min，选择存活时间最长的
                candidates.sort(key=lambda x: x["duration"], reverse=True)
                best_link = candidates[0]["link"]
                
            if best_link not in retained_links:
                retained_links.append(best_link)

        # 覆写数据并保存
        data["links-add"] = retained_links
        output_path = os.path.join(args.output_dir, os.path.basename(file_path))
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    print(f"🚀 Minimum Lifetime (Threshold: {args.min_duration}s) stored in {args.output_dir}")

if __name__ == "__main__":
    main()