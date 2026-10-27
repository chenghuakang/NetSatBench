#!/usr/bin/env python3
import os
import json
import argparse
from glob import glob

def main():
    parser = argparse.ArgumentParser(description="Apply Minimum Lifetime Filter to Epochs")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save filtered epochs")
    parser.add_argument("--min-duration", type=float, default=10.0, 
                        help="Minimum remaining visibility time (T) required for Ground/User links")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    epoch_files = sorted(glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json")))

    if not epoch_files:
        print(f"❌ No epoch files found in {args.input_dir}")
        return

    filtered_out_count = 0

    for file_path in epoch_files:
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        # 适配真实格式：检查 "links-add"
        if "links-add" not in data:
            with open(os.path.join(args.output_dir, os.path.basename(file_path)), 'w') as f:
                json.dump(data, f, indent=2)
            continue
            
        links = data["links-add"]
        retained_links = []
        
        for link in links:
            # 读取 endpoint1 和 endpoint2
            ep1 = link.get("endpoint1", "")
            ep2 = link.get("endpoint2", "")
            
            # 判断是否为纯星间链路 (ISL)
            is_sat_sat = ("sat" in ep1) and ("sat" in ep2)
            
            if is_sat_sat:
                # 星间链路即使 expected_duration 是 null，也无条件保留
                retained_links.append(link)
            else:
                # 涉及到地面站(grd)或用户(usr)的天地链路
                duration = link.get("expected_duration")
                
                # 容错处理：如果碰巧为 null，视作 0.0 丢弃；否则转为 float
                duration_val = float(duration) if duration is not None else 0.0
                
                # 如果剩余存活时间大于等于阈值 T，则保留；否则抛弃
                if duration_val >= args.min_duration:
                    retained_links.append(link)
                else:
                    filtered_out_count += 1

        # 更新并覆盖原来的 links-add
        data["links-add"] = retained_links
        
        output_path = os.path.join(args.output_dir, os.path.basename(file_path))
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    print(f"🎉 Minimum Lifetime (T={args.min_duration}s) applied to {args.output_dir}")
    print(f"ℹ️  Total {filtered_out_count} short-lived Ground/User links were discarded.")

if __name__ == "__main__":
    main()