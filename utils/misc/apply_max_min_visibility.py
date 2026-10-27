#!/usr/bin/env python3
import os
import json
import argparse
from glob import glob

def main():
    parser = argparse.ArgumentParser(description="Apply Max-min Visibility Filter to Epochs")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save filtered epochs")
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
        node_candidates = {}
        links = data["links"]
        
        for link in links:
            src, dst = link["src"], link["dst"]
            duration = link.get("expected_duration", link.get("duration", 0))
            
            for node in [src, dst]:
                if "usr" in node or "grd" in node:
                    if node not in node_candidates:
                        node_candidates[node] = []
                    node_candidates[node].append((duration, link))
        retained_links = []
        for link in links:
            if "sat" in link["src"] and "sat" in link["dst"]:
                retained_links.append(link)

        for node, candidates in node_candidates.items():
            if not candidates:
                continue
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_duration, best_link = candidates[0]
            
            if best_link not in retained_links:
                retained_links.append(best_link)

        data["links"] = retained_links
        
        output_path = os.path.join(args.output_dir, os.path.basename(file_path))
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    print(f"🎉 Max-min Visibility {args.output_dir}")

if __name__ == "__main__":
    main()
