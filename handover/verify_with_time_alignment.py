#!/usr/bin/env python3
import os
import json
import argparse
import heapq
import re
import csv
from glob import glob
from collections import defaultdict
from itertools import combinations

def parse_delay(val):
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
        return num * 1000.0
    return num

def parse_bw(val):
    if val is None:
        return 100.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).lower()
    match = re.search(r'([\d\.]+)', val_str)
    if not match:
        return 100.0
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

def get_link_id(ep1: str, ep2: str):
    return tuple(sorted([ep1, ep2]))

def is_satellite(node_name: str, sat_prefix: str = "sat") -> bool:
    return node_name.lower().startswith(sat_prefix.lower())

def extract_timestamp_or_metadata(json_data):
    """Try to extract physical timestamp if available in JSON."""
    for key in ["time", "timestamp", "sim_time", "epoch_time", "real_epoch"]:
        if key in json_data:
            return json_data[key]
    return None

def dijkstra_eval(active_graph, start, end):
    graph = defaultdict(dict)
    for (u, v), props in active_graph.items():
        graph[u][v] = props
        graph[v][u] = props

    queue = [(0.0, start, [start])]
    seen = set()
    min_dist = {start: 0.0}

    while queue:
        cost, node, path = heapq.heappop(queue)
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

def evaluate_circuit_via_sat(active_raw_graph, src, dst, sat_src, sat_dst):
    temp_graph = {}
    for link, props in active_raw_graph.items():
        u, v = link
        if u == src or v == src:
            peer = v if u == src else u
            if peer != sat_src:
                continue
        if u == dst or v == dst:
            peer = v if u == dst else u
            if peer != sat_dst:
                continue
        temp_graph[link] = props

    return dijkstra_eval(temp_graph, src, dst)

def load_policy_timeline(policy_dir, targets, sat_prefix="sat"):
    """
    Loads pruned policy data with per-epoch active satellites and metadata.
    """
    epoch_files = glob(os.path.join(policy_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)
    active_graph = {}
    records = {}

    for file_path in epoch_files:
        e_idx = get_epoch_number(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        ts = extract_timestamp_or_metadata(data)

        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                active_graph.pop(get_link_id(ep1, ep2), None)

        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                active_graph[get_link_id(ep1, ep2)] = True

        node_sats = {}
        for node in targets:
            connected = set()
            for (u, v) in active_graph.keys():
                if u == node and is_satellite(v, sat_prefix):
                    connected.add(v)
                elif v == node and is_satellite(u, sat_prefix):
                    connected.add(u)
            node_sats[node] = frozenset(connected)

        records[e_idx] = {
            "timestamp": ts,
            "selections": node_sats,
            # Create a fingerprint of non-access ISL links to match raw epoch
            "isl_fingerprint": frozenset(l for l in active_graph.keys() if not any(t in l for t in targets))
        }

    return records

def build_raw_epoch_mapping(raw_dir, sample_policy_records, targets, sat_prefix="sat"):
    """
    Determines the alignment mapping: sub_epoch_id -> raw_epoch_id.
    """
    raw_files = glob(os.path.join(raw_dir, "NetSatBench-epoch*.json"))
    raw_files.sort(key=get_epoch_number)

    # Check if first file contains timestamp
    with open(raw_files[0], 'r', encoding='utf-8') as f:
        first_raw = json.load(f)
    has_ts = extract_timestamp_or_metadata(first_raw) is not None

    alignment_map = {} # sub_idx -> raw_idx

    if has_ts:
        print("[*] Timestamp field detected! Aligning by physical simulation timestamp...")
        raw_ts_to_idx = {}
        for f in raw_files:
            r_idx = get_epoch_number(f)
            with open(f, 'r', encoding='utf-8') as jf:
                d = json.load(jf)
                ts = extract_timestamp_or_metadata(d)
                if ts is not None:
                    raw_ts_to_idx[ts] = r_idx
        for sub_idx, info in sample_policy_records.items():
            if info["timestamp"] in raw_ts_to_idx:
                alignment_map[sub_idx] = raw_ts_to_idx[info["timestamp"]]
    else:
        print("[*] No timestamp field in JSON. Performing ISL Topology State Alignment across 24,014 raw epochs...")
        raw_active_graph = {}
        sub_indices = sorted(sample_policy_records.keys())
        sub_curr_ptr = 0
        total_sub = len(sub_indices)

        for f in raw_files:
            r_idx = get_epoch_number(f)
            with open(f, 'r', encoding='utf-8') as jf:
                d = json.load(jf)
            for link in d.get("links-del", []):
                ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
                if ep1 and ep2:
                    raw_active_graph.pop(get_link_id(ep1, ep2), None)
            for link in d.get("links-add", []):
                ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
                if ep1 and ep2:
                    raw_active_graph[get_link_id(ep1, ep2)] = True

            # Match ISL core topology
            if sub_curr_ptr < total_sub:
                sub_idx = sub_indices[sub_curr_ptr]
                target_isl = sample_policy_records[sub_idx]["isl_fingerprint"]
                current_isl = frozenset(l for l in raw_active_graph.keys() if not any(t in l for t in targets))
                
                # Check match
                if current_isl == target_isl:
                    alignment_map[sub_idx] = r_idx
                    sub_curr_ptr += 1

    print(f"[+] Time Alignment Completed: Successfully mapped {len(alignment_map)} / {len(sample_policy_records)} pruned epochs to raw epochs.")
    return alignment_map

def main():
    parser = argparse.ArgumentParser(
        description="Verify against raw unpruned epochs with strict TIME-ALIGNMENT (handling reindexed/pruned epochs)."
    )
    parser.add_argument("--raw-dir", required=True, help="Path to single unpruned raw directory (e.g., 24014 epochs)")
    parser.add_argument("--policy-dirs", nargs="+", required=True, help="Pruned policy directories (e.g., 3891 epochs)")
    parser.add_argument("--src", type=str, default="usr1", help="Source node")
    parser.add_argument("--dst", type=str, default="grd7", help="Destination node")
    parser.add_argument("--sat-prefix", type=str, default="sat", help="Satellite prefix")
    parser.add_argument("--delay-key", type=str, default="delay", help="Delay key")
    parser.add_argument("--bw-key", type=str, default="rate", help="Bandwidth key")
    parser.add_argument("--tie-tol", type=float, default=0.01, help="Tolerance for metric equality (default: 0.01)")
    parser.add_argument("--export-csv", type=str, default="aligned_unpruned_divergence.csv", help="CSV export filename")
    args = parser.parse_args()

    targets = [args.src, args.dst]
    policies_data = {}

    print("[1/3] Loading pruned policy epochs...")
    for p_dir in args.policy_dirs:
        p_name = os.path.basename(os.path.normpath(p_dir))
        data = load_policy_timeline(p_dir, targets, args.sat_prefix)
        policies_data[p_name] = data
        print(f"    - Loaded '{p_name}': {len(data)} epochs.")

    valid_policies = list(policies_data.keys())
    if len(valid_policies) < 2:
        print("[!] Error: At least two policies required.")
        return

    # 2. Build Alignment Map between Sub-Epochs and Raw-Epochs
    print("\n[2/3] Establishing Temporal Alignment between Pruned and Raw Epochs...")
    first_p_name = valid_policies[0]
    alignment_map = build_raw_epoch_mapping(args.raw_dir, policies_data[first_p_name], targets, args.sat_prefix)

    if not alignment_map:
        print("[!] Fatal: Failed to establish time alignment. Please ensure raw and sub directories match.")
        return

    # Invert mapping: raw_epoch_id -> list of sub_epoch_ids
    raw_to_sub = defaultdict(list)
    for sub_idx, raw_idx in alignment_map.items():
        raw_to_sub[raw_idx].append(sub_idx)

    max_aligned_raw = max(alignment_map.values())

    # 3. Replay Raw Graph and perform diagnosis on Aligned Epochs
    print(f"\n[3/3] Replaying Raw Graph up to Aligned Raw Epoch {max_aligned_raw}...")
    raw_files = glob(os.path.join(args.raw_dir, "NetSatBench-epoch*.json"))
    raw_files.sort(key=get_epoch_number)

    active_raw_graph = {}
    divergence_records = defaultdict(list)
    csv_rows = []

    for file_path in raw_files:
        r_idx = get_epoch_number(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            d = json.load(f)

        for link in d.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                active_raw_graph.pop(get_link_id(ep1, ep2), None)

        for link in d.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                d_val = parse_delay(link.get(args.delay_key, 1.0))
                b_val = parse_bw(link.get(args.bw_key, 100.0))
                active_raw_graph[get_link_id(ep1, ep2)] = {'delay': d_val, 'bw': b_val}

        if r_idx in raw_to_sub:
            for sub_e in raw_to_sub[r_idx]:
                # Extract truly visible candidate satellites at this aligned moment
                raw_cand_src = {}
                raw_cand_dst = {}
                for (u, v), props in active_raw_graph.items():
                    if u == args.src and is_satellite(v, args.sat_prefix):
                        raw_cand_src[v] = props
                    elif v == args.src and is_satellite(u, args.sat_prefix):
                        raw_cand_src[u] = props
                    if u == args.dst and is_satellite(v, args.sat_prefix):
                        raw_cand_dst[v] = props
                    elif v == args.dst and is_satellite(u, args.sat_prefix):
                        raw_cand_dst[u] = props

                # Evaluate decisions across pairs
                for p1, p2 in combinations(valid_policies, 2):
                    p1_sel = policies_data[p1][sub_e]["selections"]
                    p2_sel = policies_data[p2][sub_e]["selections"]

                    s1_src = next(iter(p1_sel.get(args.src, set())), None)
                    s1_dst = next(iter(p1_sel.get(args.dst, set())), None)
                    s2_src = next(iter(p2_sel.get(args.src, set())), None)
                    s2_dst = next(iter(p2_sel.get(args.dst, set())), None)

                    if s1_src != s2_src or s1_dst != s2_dst:
                        cost1, bw1, path1 = evaluate_circuit_via_sat(active_raw_graph, args.src, args.dst, s1_src, s1_dst)
                        cost2, bw2, path2 = evaluate_circuit_via_sat(active_raw_graph, args.src, args.dst, s2_src, s2_dst)

                        delay_gap = abs(cost1 - cost2) if (path1 and path2) else float('inf')
                        bw_gap = abs(bw1 - bw2) if (path1 and path2) else float('inf')
                        hops1 = max(0, len(path1) - 3) if len(path1) >= 3 else -1
                        hops2 = max(0, len(path2) - 3) if len(path2) >= 3 else -1
                        hop_gap = abs(hops1 - hops2) if (path1 and path2) else -1

                        is_tie = (delay_gap <= args.tie_tol and bw_gap <= args.tie_tol and hop_gap == 0)

                        record = {
                            "sub_epoch": sub_e,
                            "raw_epoch": r_idx,
                            "p1": p1, "p2": p2,
                            "p1_sats": (s1_src, s1_dst),
                            "p2_sats": (s2_src, s2_dst),
                            "path1": " -> ".join(path1) if path1 else "<No Route>",
                            "path2": " -> ".join(path2) if path2 else "<No Route>",
                            "cost1": cost1, "cost2": cost2,
                            "delay_gap": delay_gap,
                            "bw1": bw1, "bw2": bw2,
                            "hops1": hops1, "hops2": hops2,
                            "hop_gap": hop_gap,
                            "is_tie": is_tie,
                            "cand_src": list(raw_cand_src.keys()),
                            "cand_dst": list(raw_cand_dst.keys())
                        }
                        divergence_records[(p1, p2)].append(record)

                        csv_rows.append({
                            "Policy_1": p1, "Policy_2": p2,
                            "Sub_Epoch": sub_e, "Aligned_Raw_Epoch": r_idx,
                            "P1_Selected": f"src:{s1_src}|dst:{s1_dst}",
                            "P2_Selected": f"src:{s2_src}|dst:{s2_dst}",
                            "Raw_Candidates_Src": ";".join(raw_cand_src.keys()),
                            "Raw_Candidates_Dst": ";".join(raw_cand_dst.keys()),
                            "Is_Candidate_Metric_Tie": is_tie,
                            "P1_Circuit_Path": " -> ".join(path1) if path1 else "<No Route>",
                            "P2_Circuit_Path": " -> ".join(path2) if path2 else "<No Route>",
                            "P1_E2E_Delay_ms": f"{cost1:.4f}" if path1 else "INF",
                            "P2_E2E_Delay_ms": f"{cost2:.4f}" if path2 else "INF",
                            "E2E_Delay_Gap_ms": f"{delay_gap:.4f}" if delay_gap != float('inf') else "N/A",
                            "P1_ISL_Hops": hops1, "P2_ISL_Hops": hops2,
                            "Hop_Gap": hop_gap,
                            "P1_BW_Mbps": bw1, "P2_BW_Mbps": bw2
                        })

        if r_idx >= max_aligned_raw:
            break

    # 4. Final Aligned Report
    print("\n" + "=" * 82)
    print(" TIME-ALIGNED RAW VERIFICATION REPORT")
    print("=" * 82)
    print(f"Total Evaluated Aligned Epochs : {len(alignment_map)}")
    print(f"Metric Tie Tolerance (epsilon) : {args.tie_tol}")
    print("-" * 82)

    for (p1, p2), records in divergence_records.items():
        total_div = len(records)
        ties = sum(1 for r in records if r["is_tie"])
        diffs = total_div - ties

        print(f"\n[Comparison Pair: {p1} vs {p2}]")
        print(f"  * Total Divergent Epochs : {total_div} / {len(alignment_map)} ({(total_div/len(alignment_map)*100.0):.2f}%)")
        print(f"  * Pruning Tie Artifacts  : {ties} ({(ties/total_div*100.0 if total_div else 0.0):.2f}%)")
        print(f"  * True Metric Trade-offs : {diffs} ({(diffs/total_div*100.0 if total_div else 0.0):.2f}%)")

        print(f"\n  [Sample Time-Aligned Circuit Trace (First 3 Epochs)]:")
        for r in records[:3]:
            print(f"   --- Sub-Epoch {r['sub_epoch']} (Aligned Raw Epoch: {r['raw_epoch']}) ---")
            print(f"   Raw Visible Candidates : usr1 -> {r['cand_src']} | grd7 -> {r['cand_dst']}")
            print(f"   [{p1}] Selection : src:{r['p1_sats'][0]}, dst:{r['p1_sats'][1]}")
            print(f"       Raw Circuit  : {r['path1']}")
            print(f"       Metrics      : E2E Delay = {r['cost1']:.3f} ms | ISL Hops = {r['hops1']} | BW = {r['bw1']:.1f} Mbps")
            print(f"   [{p2}] Selection : src:{r['p2_sats'][0]}, dst:{r['p2_sats'][1]}")
            print(f"       Raw Circuit  : {r['path2']}")
            print(f"       Metrics      : E2E Delay = {r['cost2']:.3f} ms | ISL Hops = {r['hops2']} | BW = {r['bw2']:.1f} Mbps")
            diag = "PRUNING TIE (Equal Metrics)" if r["is_tie"] else f"GENUINE METRIC GAP (Delay Diff: {r['delay_gap']:.3f} ms, Hop Diff: {r['hop_gap']})"
            print(f"       ==> Diagnosis: {diag}")

    if csv_rows and args.export_csv:
        fieldnames = [
            "Policy_1", "Policy_2", "Sub_Epoch", "Aligned_Raw_Epoch",
            "P1_Selected", "P2_Selected", "Raw_Candidates_Src", "Raw_Candidates_Dst",
            "Is_Candidate_Metric_Tie", "P1_Circuit_Path", "P2_Circuit_Path",
            "P1_E2E_Delay_ms", "P2_E2E_Delay_ms", "E2E_Delay_Gap_ms",
            "P1_ISL_Hops", "P2_ISL_Hops", "Hop_Gap", "P1_BW_Mbps", "P2_BW_Mbps"
        ]
        with open(args.export_csv, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\n[+] Aligned diagnostic CSV saved to: {args.export_csv}")

    print("\n" + "=" * 82 + "\n")

if __name__ == "__main__":
    main()