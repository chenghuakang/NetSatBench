#!/usr/bin/env python3
import os
import json
import argparse
from glob import glob

def main():
    parser = argparse.ArgumentParser(description="Apply Minimum Lifetime Filter to Epochs")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save filtered epochs")
    parser.add_argument("--t-lt", type=float, default=60.0, help="Minimum lifetime threshold in seconds")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    epoch_files = sorted(glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json")))

    if not epoch_files:
        print(f"❌ No epoch files found in {args.input_dir}")
        return

    for file_path in epoch_files:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        if "links" not in data:
            with open(os.path.join(args.output_dir, os.path.basename(file_path)), 'w') as f:
                json.dump(data, f, indent=2)
            continue

        links = data["links"]
        
        node_lifetimes = {}
        for link in links:
            src, dst = link["src"], link["dst"]

            duration = link.get("expected_duration", link.get("duration", 0))
            
            for node in [src, dst]:
                if "usr" in node or "grd" in node:
                    node_lifetimes[node] = duration

        retained_links = []
        for link in links:
            src, dst = link["src"], link["dst"]
            
            if "sat" in src and "sat" in dst:
                retained_links.append(link)
                continue
                
            is_valid = True
            for node in [src, dst]:
                if "usr" in node or "grd" in node:
                    if node_lifetimes.get(node, 0) < args.t_lt:
                        is_valid = False
            
            if is_valid:
                retained_links.append(link)

        data["links"] = retained_links
        output_path = os.path.join(args.output_dir, os.path.basename(file_path))
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    print(f"🎉 Minimum Lifetime  (T_lt = {args.t_lt}s)！: {args.output_dir}")

if __name__ == "__main__":
    main()
