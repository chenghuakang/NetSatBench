#!/usr/bin/env python3
import argparse
import json
import re
import copy
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce min-lifetime/max-min-visibility handover policies on annotated epochs.")
    parser.add_argument("--epochs-dir", required=True, help="Directory containing annotated NetSatBench epoch JSON files")
    parser.add_argument("--output-dir", required=True, help="Output directory for the policy-enforced epoch files")
    parser.add_argument("--file-pattern", default="NetSatBench-epoch*.json", help="Epoch filename pattern")
    parser.add_argument("--indent", type=int, default=2, help="Indentation for JSON output")
    return parser.parse_args()

def parse_epoch_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def last_numeric_suffix(path: Path) -> int:
    matches = re.findall(r"(\d+)", path.name)
    return int(matches[-1]) if matches else -1

def is_sat(node_name: str) -> bool:
    return "sat" in str(node_name).lower()

def is_gsl(ep1: str, ep2: str) -> bool:
    return is_sat(ep1) != is_sat(ep2)

def get_g_s(ep1: str, ep2: str) -> Tuple[str, str]:
    if is_sat(ep1):
        return ep2, ep1
    return ep1, ep2

def main() -> None:
    args = parse_args()
    source_dir = Path(args.epochs_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(source_dir.glob(args.file_pattern), key=last_numeric_suffix)
    if not files:
        raise SystemExit(f"No JSON epoch files found in: {source_dir}")

    # Tracking state
    visible_sats: Dict[str, Dict[str, datetime]] = defaultdict(dict)  # ground -> {sat: expiration_time}
    saved_link_props: Dict[str, Dict[str, dict]] = defaultdict(dict)  # ground -> {sat: latest_link_obj}
    current_conn: Dict[str, str] = {}                                 # ground -> active_sat

    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
        
        epoch_time = parse_epoch_time(str(doc["time"]))
        
        new_adds, new_dels, new_updates = [], [], []

        # 1. Process Deletions
        for del_obj in doc.get("links-del", []):
            ep1, ep2 = str(del_obj.get("endpoint1", "")), str(del_obj.get("endpoint2", ""))
            if is_gsl(ep1, ep2):
                g, s = get_g_s(ep1, ep2)
                visible_sats[g].pop(s, None)
                if current_conn.get(g) == s:
                    new_dels.append(del_obj)
                    del current_conn[g]
            else:
                new_dels.append(del_obj) # Keep ISLs intact

        # 2. Process Additions
        for add_obj in doc.get("links-add", []):
            ep1, ep2 = str(add_obj.get("endpoint1", "")), str(add_obj.get("endpoint2", ""))
            if is_gsl(ep1, ep2):
                g, s = get_g_s(ep1, ep2)
                dur = add_obj.get("expected_duration")
                expiration = epoch_time + timedelta(seconds=dur) if dur is not None else datetime.max
                visible_sats[g][s] = expiration
                saved_link_props[g][s] = copy.deepcopy(add_obj)
            else:
                new_adds.append(add_obj) # Keep ISLs intact

        # 3. Process Updates
        for up_obj in doc.get("links-update", []):
            ep1, ep2 = str(up_obj.get("endpoint1", "")), str(up_obj.get("endpoint2", ""))
            if is_gsl(ep1, ep2):
                g, s = get_g_s(ep1, ep2)
                saved_link_props[g][s] = copy.deepcopy(up_obj)
                if current_conn.get(g) == s:
                    new_updates.append(up_obj)
            else:
                new_updates.append(up_obj)

        # 4. Make Handover Decisions (Max-Min Visibility / Min Lifetime selection)
        for g, sats in visible_sats.items():
            if g not in current_conn and sats:
                # Select the satellite with the maximum expiration time
                best_s = max(sats.keys(), key=lambda s: sats[s])
                current_conn[g] = best_s
                
                # Emit a links-add event for this newly chosen connection
                add_event = saved_link_props[g][best_s]
                # Clean annotation to keep engine pure
                add_event.pop("expected_duration", None) 
                new_adds.append(add_event)

        # 5. Overwrite and Save
        doc["links-del"] = new_dels
        doc["links-add"] = new_adds
        doc["links-update"] = new_updates

        dest_path = output_dir / path.name
        with dest_path.open("w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=args.indent)
            fh.write("\n")

        print(f"Processed {path.name} -> Active Ground Connections: {len(current_conn)}")

if __name__ == "__main__":
    main()