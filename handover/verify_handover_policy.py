#!/usr/bin/env python3
import os
import json
import argparse
import re
import math
import statistics
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

def extract_connected_satellites_with_metrics(active_graph, target_node: str, sat_prefix: str = "sat"):
    """
    Extracts all directly connected satellites and their link properties (delay, bw).
    Returns: dict mapping sat_node -> {'delay': float, 'bw': float}
    """
    res = {}
    for (u, v), props in active_graph.items():
        if u == target_node and is_satellite(v, sat_prefix):
            res[v] = props
        elif v == target_node and is_satellite(u, sat_prefix):
            res[u] = props
    return res

def build_policy_trajectory_with_metrics(policy_dir: str, target_nodes: list, sat_prefix: str = "sat", 
                                        delay_key="delay", bw_key="rate"):
    """
    Replay incremental topology changes epoch-by-epoch.
    Returns:
      trajectory: dict[epoch_num] -> frozenset of selected satellites across targets
      epoch_metrics: dict[epoch_num] -> dict[node] -> dict[sat] -> {'delay': ..., 'bw': ...}
    """
    epoch_files = glob(os.path.join(policy_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)

    if not epoch_files:
        return {}, {}

    active_graph = {}
    trajectory = {}
    epoch_metrics = {}

    for file_path in epoch_files:
        epoch_idx = get_epoch_number(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 1. Process deletions
        for link in data.get("links-del", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                active_graph.pop(get_link_id(ep1, ep2), None)

        # 2. Process additions
        for link in data.get("links-add", []):
            ep1, ep2 = link.get("endpoint1"), link.get("endpoint2")
            if ep1 and ep2:
                link_id = get_link_id(ep1, ep2)
                d_val = parse_delay(link.get(delay_key, 1.0))
                b_val = parse_bw(link.get(bw_key, 100.0))
                active_graph[link_id] = {'delay': d_val, 'bw': b_val}

        # 3. Capture selections and per-node metric snapshots
        epoch_selected_sats = set()
        node_sat_map = {}
        for node in target_nodes:
            sats_with_metrics = extract_connected_satellites_with_metrics(active_graph, node, sat_prefix)
            node_sat_map[node] = sats_with_metrics
            epoch_selected_sats.update(sats_with_metrics.keys())

        trajectory[epoch_idx] = frozenset(epoch_selected_sats)
        epoch_metrics[epoch_idx] = node_sat_map

    return trajectory, epoch_metrics

def analyze_divergence_metrics(common_epochs, p1_name, p2_name, traj1, traj2, metrics1, metrics2, target_nodes, tol=0.01):
    """
    Analyzes epochs where policy decisions diverge.
    Differentiates between:
      1. Tie-breaking (metrics identical within 0.01 tolerance).
      2. True metric trade-offs (quantifies mean difference and relative deviation).
    """
    divergent_epochs = [e for e in common_epochs if traj1[e] != traj2[e]]
    if not divergent_epochs:
        return None

    tie_count = 0
    true_diff_count = 0
    delay_diffs = []
    delay_relative_diffs = []
    bw_diffs = []
    bw_relative_diffs = []

    for e in divergent_epochs:
        is_epoch_tie = True
        
        for node in target_nodes:
            m1 = metrics1[e].get(node, {})
            m2 = metrics2[e].get(node, {})

            sats1 = set(m1.keys())
            sats2 = set(m2.keys())

            if sats1 != sats2:
                if sats1 and sats2:
                    d1 = statistics.mean([m1[s]['delay'] for s in sats1])
                    b1 = statistics.mean([m1[s]['bw'] for s in sats1])
                    d2 = statistics.mean([m2[s]['delay'] for s in sats2])
                    b2 = statistics.mean([m2[s]['bw'] for s in sats2])

                    abs_d_diff = abs(d1 - d2)
                    mean_d = (d1 + d2) / 2.0
                    rel_d_diff = (abs_d_diff / mean_d * 100.0) if mean_d > 0 else 0.0

                    abs_b_diff = abs(b1 - b2)
                    mean_b = (b1 + b2) / 2.0
                    rel_b_diff = (abs_b_diff / mean_b * 100.0) if mean_b > 0 else 0.0

                    # 只要时延差或带宽差超过容差 0.01，即认定存在实质差异
                    if abs_d_diff > tol or abs_b_diff > tol:
                        is_epoch_tie = False
                        delay_diffs.append(abs_d_diff)
                        delay_relative_diffs.append(rel_d_diff)
                        bw_diffs.append(abs_b_diff)
                        bw_relative_diffs.append(rel_b_diff)
                else:
                    is_epoch_tie = False

        if is_epoch_tie:
            tie_count += 1
        else:
            true_diff_count += 1

    return {
        "divergent_epochs_count": len(divergent_epochs),
        "tie_count": tie_count,
        "true_diff_count": true_diff_count,
        "mean_delay_diff": statistics.mean(delay_diffs) if delay_diffs else 0.0,
        "max_delay_diff": max(delay_diffs) if delay_diffs else 0.0,
        "mean_delay_rel_diff": statistics.mean(delay_relative_diffs) if delay_relative_diffs else 0.0,
        "mean_bw_diff": statistics.mean(bw_diffs) if bw_diffs else 0.0,
        "max_bw_diff": max(bw_diffs) if bw_diffs else 0.0,
        "mean_bw_rel_diff": statistics.mean(bw_relative_diffs) if bw_relative_diffs else 0.0
    }

def main():
    parser = argparse.ArgumentParser(
        description="Verify handover policies with deep tie-breaking and metric divergence analysis."
    )
    parser.add_argument("--policy-dirs", nargs="+", required=True, help="Paths to policy directories")
    parser.add_argument("--targets", nargs="+", default=["usr1", "grd7"], help="Target nodes to track")
    parser.add_argument("--sat-prefix", type=str, default="sat", help="Satellite name prefix")
    parser.add_argument("--delay-key", type=str, default="delay", help="JSON field for link delay")
    parser.add_argument("--bw-key", type=str, default="rate", help="JSON field for link rate/bw")
    parser.add_argument("--high-similarity-threshold", type=float, default=70.0,
                        help="Agreement rate percentage threshold to trigger deep metric analysis (default: 70%%)")
    parser.add_argument("--tie-tol", type=float, default=0.01, help="Tolerance for declaring metric ties (default: 0.01)")
    args = parser.parse_args()

    trajectories = {}
    all_metrics = {}
    print(f"[*] Replaying topology and recording metrics (Tie Tolerance = {args.tie_tol})...")

    for p_dir in args.policy_dirs:
        p_name = os.path.basename(os.path.normpath(p_dir))
        traj, m = build_policy_trajectory_with_metrics(
            p_dir, args.targets, args.sat_prefix, args.delay_key, args.bw_key
        )
        if not traj:
            print(f"[!] Warning: No epoch files found in {p_dir}")
            continue
        trajectories[p_name] = traj
        all_metrics[p_name] = m
        print(f"    - Loaded '{p_name}': {len(traj)} epochs.")

    valid_policies = list(trajectories.keys())
    if len(valid_policies) < 2:
        print("[!] Error: At least two valid policy datasets are required.")
        return

    common_epochs = sorted(set.intersection(*[set(trajectories[p].keys()) for p in valid_policies]))

    print("\n" + "=" * 78)
    print(" ADVANCED HANDOVER VERIFICATION & DIVERGENCE ANALYSIS REPORT")
    print("=" * 78)
    print(f"Monitored Nodes           : {', '.join(args.targets)}")
    print(f"Evaluated Common Epochs   : {len(common_epochs)} (Epoch {common_epochs[0]} to {common_epochs[-1]})")
    print(f"Tie Tolerance             : {args.tie_tol}")
    print(f"High-Similarity Trigger   : >= {args.high_similarity_threshold:.1f}% Agreement Rate")
    print("-" * 78)

    # 1. Identical Policy Groups
    identical_groups = defaultdict(list)
    for p in valid_policies:
        seq = tuple(trajectories[p][e] for e in common_epochs)
        identical_groups[seq].append(p)

    print("\n[1. Fully Identical Policies (100% Exact Match Across All Epochs)]")
    found_any_identical = False
    for group in identical_groups.values():
        if len(group) > 1:
            found_any_identical = True
            print(f"  * Matched Group: {', '.join(group)}")
    if not found_any_identical:
        print("  None.")

    # 2. Pairwise Agreement Matrix
    print("\n[2. Pairwise Decision Agreement Rates]")
    header = f"{'Policy Pair':<50} | {'Identical':<12} | {'Agreement'}"
    print(header)
    print("-" * len(header))

    pair_results = []
    for p1, p2 in combinations(valid_policies, 2):
        matches = sum(1 for e in common_epochs if trajectories[p1][e] == trajectories[p2][e])
        rate = (matches / len(common_epochs) * 100.0) if common_epochs else 0.0
        pair_results.append((p1, p2, matches, rate))
        print(f"{p1 + ' vs ' + p2:<50} | {matches}/{len(common_epochs)}  | {rate:>6.2f}%")

    # 3. Deep Analysis for High-Similarity Pairs
    print("\n[3. Deep Divergence Analysis for High-Similarity Pairs (Rate >= Threshold)]")
    analyzed_high_sim = False

    for p1, p2, matches, rate in pair_results:
        if rate >= args.high_similarity_threshold and rate < 100.0:
            analyzed_high_sim = True
            div_stats = analyze_divergence_metrics(
                common_epochs, p1, p2,
                trajectories[p1], trajectories[p2],
                all_metrics[p1], all_metrics[p2],
                args.targets, tol=args.tie_tol
            )

            print(f"\n--- Investigating Pair: [{p1}] vs [{p2}] ---")
            print(f"  Overall Agreement Rate : {rate:.2f}% ({matches}/{len(common_epochs)} epochs identical)")
            print(f"  Divergent Epochs Count : {div_stats['divergent_epochs_count']}")
            print(f"  - Equal Metric Ties    : {div_stats['tie_count']} epochs "
                  f"({(div_stats['tie_count'] / div_stats['divergent_epochs_count'] * 100.0):.2f}% of divergences were pure random/order ties)")
            print(f"  - True Metric Difference: {div_stats['true_diff_count']} epochs")

            if div_stats['true_diff_count'] > 0:
                print(f"  [Metric Deviation on Disagreeing Epochs]:")
                print(f"    * Delay Absolute Gap  : Mean = {div_stats['mean_delay_diff']:.4f} ms (Max = {div_stats['max_delay_diff']:.4f} ms)")
                print(f"    * Delay Relative Gap  : Mean = {div_stats['mean_delay_rel_diff']:.2f}%")
                print(f"    * Bandwidth Gap       : Mean = {div_stats['mean_bw_diff']:.2f} Mbps (Max = {div_stats['max_bw_diff']:.2f} Mbps)")
                print(f"    * Bandwidth Relative  : Mean = {div_stats['mean_bw_rel_diff']:.2f}%")
            else:
                print("  --> ALL divergences were due to exact metric ties (random tie-breaking artifacts)!")

    if not analyzed_high_sim:
        print("  No non-identical policy pair exceeded the high similarity threshold.")

    print("\n" + "=" * 78 + "\n")

if __name__ == "__main__":
    main()