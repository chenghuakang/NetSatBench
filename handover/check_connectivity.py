#!/usr/bin/env python3
"""
离线检查 epoch 目录: 逐帧回放 links-add / links-del / links-update,
判断每个 epoch 之后 src -> dst 在链路图里是否连通 (不需要 docker / nsb).

用法:
  python3 check_connectivity.py <epoch_dir> [--src usr1] [--dst grd7] [--win 100]
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import deque


def epoch_no(path):
    m = re.search(r"epoch(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else -1


def key(link):
    return tuple(sorted((link["endpoint1"], link["endpoint2"])))


def apply_epoch(active, data):
    # 顺序与预检脚本保持一致: del -> add -> update
    for l in data.get("links-del", []):
        active.pop(key(l), None)
    for l in data.get("links-add", []):
        active[key(l)] = dict(l)
    for l in data.get("links-update", []):
        k = key(l)
        if k in active:
            active[k].update(l)


def hop_count(active, src, dst):
    """BFS, 返回 src->dst 的最少跳数; 不连通返回 None."""
    adj = {}
    for a, b in active:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    if src not in adj or dst not in adj:
        return None
    prev = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        if u == dst:
            hops = 0
            while prev[u] is not None:
                u = prev[u]
                hops += 1
            return hops
        for v in adj[u]:
            if v not in prev:
                prev[v] = u
                q.append(v)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("epoch_dir")
    ap.add_argument("--src", default="usr1")
    ap.add_argument("--dst", default="grd7")
    ap.add_argument("--win", type=int, default=100, help="需要的连续连通帧数")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.epoch_dir, "NetSatBench-epoch*.json")),
                   key=epoch_no)
    if not files:
        print(f"没有在 {args.epoch_dir} 找到 epoch 文件")
        sys.exit(1)

    active = {}
    hops = []
    for p in files:
        with open(p) as f:
            apply_epoch(active, json.load(f))
        hops.append(hop_count(active, args.src, args.dst))

    # 把结果压成连续区间输出 (区间用的是排序后的下标 idx, 括号里是文件名里的 epoch 号)
    print(f"{args.src} -> {args.dst}, 共 {len(files)} 个 epoch\n")
    start = 0
    for i in range(1, len(hops) + 1):
        if i == len(hops) or (hops[i] is None) != (hops[start] is None):
            seg = hops[start:i]
            e0, e1 = epoch_no(files[start]), epoch_no(files[i - 1])
            if seg[0] is None:
                print(f"idx {start:>4}-{i-1:<4} (epoch{e0}-{e1}): 不连通")
            else:
                print(f"idx {start:>4}-{i-1:<4} (epoch{e0}-{e1}): 连通, 最短跳数 {min(seg)}-{max(seg)}")
            start = i

    # 找第一个长度 >= win 的连续连通窗口
    run = 0
    found = None
    for i, h in enumerate(hops):
        run = run + 1 if h is not None else 0
        if run >= args.win:
            found = i - args.win + 1
            break

    print()
    if found is None:
        print(f"没有找到连续 {args.win} 帧都连通的窗口 -> 预检的固定窗口不可用")
    else:
        print(f"第一个连续 {args.win} 帧连通的窗口: start_offset = {found} "
              f"(epoch{epoch_no(files[found])})")
        print("可以把预检里的 start_offset 改成这个值")


if __name__ == "__main__":
    main()
