#!/usr/bin/env python3
"""
从原始 epoch 序列中截取一段 "目标节点持续连通" 的时间窗口，生成较短的 epoch 子集。

处理流程
  1. 逐帧回放 links-del -> links-add -> links-update，判断每一帧之后 --nodes 是否端到端连通
     (只允许经过卫星中转，不会经过其它地面站/用户节点绕路)。
  2. 选出第一个持续时间 >= --min-duration 的长连接；长连接前后间隔 <= --buffer 的短连接一并保留。
  3. 前后各留 --buffer 秒的无连接状态，得到窗口 [window_start, window_end]。
  4. 窗口之前被截掉的 epoch 折叠成新的 epoch0:
       - 链路: 窗口起点时刻真实存在的链路 (回放得到，不含历史残留)
       - 路由: 不再回放历史命令，而是根据 "窗口起点的连接模型" 重新生成注入
         (节点 IP 由节点名自动推出，并用被截掉的历史路由校验 IP 规则与选路规则)
     窗口内的 epoch 原样保留 (包括 "run" 路由注入等所有字段)，重新从 epoch0 开始编号。
  5. 回放输出，与原始时间线逐帧比对链路状态。

路由注入格式 (每个目的地: 主路径 metric 100 + 备份路径 metric 200)
  "run": {"sat1": ["extra/routing/add_ipv6_route_ll.sh vl_sat2_1 2001:db8:100::27 100; \
                    extra/routing/add_ipv6_route_ll.sh vl_sat49_1 2001:db8:100::27 200; ..."]}

用法 (在旧版基础上只多了一个可选参数 --route-dests):
  python3 extract_subset.py --input-dir RAW --output-dir OUT [--nodes usr1 grd7]
                            [--min-duration 3600] [--buffer 180] [--route-dests targets|all]
"""
import os
import sys
import json
import argparse
import re
import copy
import heapq
import ipaddress
from collections import deque
from glob import glob
from datetime import datetime, timedelta

# ----------------------------- 可调常量 -----------------------------
# 节点 IP 规则: <类型前缀>::(3 + 4*(序号-1))，例如 usr1=2001:db8:102::3, sat17=2001:db8:100::43, grd7=2001:db8:101::1b
IP_PREFIX = {"sat": "2001:db8:100::", "grd": "2001:db8:101::", "usr": "2001:db8:102::"}
IP_HOST_BASE, IP_HOST_STEP = 3, 4
PRIMARY_METRIC, BACKUP_METRIC = 100, 200
ROUTE_SCRIPT_DEFAULT = "extra/routing/add_ipv6_route_ll.sh"
ROUTE_CHUNK = 200            # 每个 run 字符串最多放多少条子命令
ROUTE_MATCH_MIN = 0.90       # 选路模型与历史路由的主路径吻合率低于它就中止
MIN_VALIDATE_PAIRS = 20      # 历史路由条目少于它时无法校验，直接用默认模型
VALIDATE_MAX_PAIRS = 20000   # 校验时最多抽样多少个 (节点, 目的地)
DELAY_KEYS = ("delay", "latency")
BACKUP_MODES = ("lfa", "second_best")
TYPE_ORDER = {"sat": 0, "grd": 1, "usr": 2}
KNOWN_KEYS = {"time", "links-add", "links-del", "links-update", "run"}
# --------------------------------------------------------------------

ROUTE_RE = re.compile(r'^(\S*add_ipv6_route\S*)\s+(vl_\S+)\s+(\S+)\s+(\d+)$')
_NAME_RE = re.compile(r'^([A-Za-z]+)(\d+)$')


def get_epoch_number(filepath: str) -> int:
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1


def is_satellite(endpoint: str) -> bool:
    return "sat" in endpoint


def parse_time(time_str: str) -> datetime:
    # 兼容 "2023-10-01T00:00:00Z"
    return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%SZ")


def pair_of(link):
    e1, e2 = link.get("endpoint1"), link.get("endpoint2")
    if e1 is None or e2 is None:
        return None
    return tuple(sorted([e1, e2]))


def node_key(name):
    m = _NAME_RE.match(name)
    if not m:
        return (9, name, 0)
    return (TYPE_ORDER.get(m.group(1), 9), m.group(1), int(m.group(2)))


# ---------------------------------------------------------------------------
# 节点 IP: 由节点名自动推出 / 由 IP 反推节点名
# ---------------------------------------------------------------------------
def node_ip(name):
    m = _NAME_RE.match(name)
    if not m or m.group(1) not in IP_PREFIX or int(m.group(2)) < 1:
        return None
    base = int(ipaddress.IPv6Address(IP_PREFIX[m.group(1)]))
    return str(ipaddress.IPv6Address(base + IP_HOST_BASE + IP_HOST_STEP * (int(m.group(2)) - 1)))


def decode_ip(ip):
    try:
        a = int(ipaddress.IPv6Address(ip))
    except ValueError:
        return None
    for typ, pre in IP_PREFIX.items():
        off = a - int(ipaddress.IPv6Address(pre))
        if IP_HOST_BASE <= off < (1 << 80) and (off - IP_HOST_BASE) % IP_HOST_STEP == 0:
            return f"{typ}{(off - IP_HOST_BASE) // IP_HOST_STEP + 1}"
    return None


# ---------------------------------------------------------------------------
# 拓扑状态: 按 del -> add -> update 的顺序回放
# ---------------------------------------------------------------------------
class Topology:
    def __init__(self):
        self.links = {}          # pair -> link dict
        self.adj = {}            # node -> set(neighbors)
        self.since = {}          # pair -> 当前这条链路是在哪个 epoch 下标建立的
        self.ever_nodes = set()
        self.anomalies = 0       # 删除不存在的链路 / 重复添加 / 更新不存在的链路

    def _up(self, pair):
        a, b = pair
        self.adj.setdefault(a, set()).add(b)
        self.adj.setdefault(b, set()).add(a)
        self.ever_nodes.update(pair)

    def _down(self, pair):
        a, b = pair
        self.adj[a].discard(b)
        self.adj[b].discard(a)

    def apply(self, data, idx):
        for link in data.get("links-del", []):
            p = pair_of(link)
            if p is None:
                continue
            if p in self.links:
                del self.links[p]
                self.since.pop(p, None)
                self._down(p)
            else:
                self.anomalies += 1
        for link in data.get("links-add", []):
            p = pair_of(link)
            if p is None:
                continue
            if p in self.links:
                self.anomalies += 1          # 重复添加: 当作刷新属性，不算重建
                self.links[p] = dict(link)
            else:
                self.links[p] = dict(link)
                self.since[p] = idx
                self._up(p)
        for link in data.get("links-update", []):
            p = pair_of(link)
            if p is None:
                continue
            if p in self.links:
                self.links[p].update(link)
            else:
                self.anomalies += 1

    def connected(self, nodes):
        """nodes 是否全部与 nodes[0] 端到端连通；中转节点只能是卫星。"""
        if len(nodes) == 1:
            return bool(self.adj.get(nodes[0]))
        src = nodes[0]
        if not self.adj.get(src):
            return False
        seen = {src}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in self.adj.get(u, ()):
                if v in seen:
                    continue
                seen.add(v)
                if is_satellite(v):          # 地面站/用户节点只能做端点，不做中转
                    q.append(v)
        return all(n in seen for n in nodes)


# ---------------------------------------------------------------------------
# 命令解析 / 历史回放
# ---------------------------------------------------------------------------
def split_cmds(cmd_str: str):
    return [c.strip() for c in cmd_str.split(";") if c.strip()]


def cmd_iface_peer(subcmd: str):
    """子命令带有 vl_<peer>_<idx> 接口参数时返回 (peer, iface)，否则 None"""
    for tok in subcmd.split():
        m = re.fullmatch(r'vl_(.+)_\d+', tok)
        if m:
            return m.group(1), tok
    return None


def replay_prefix(epochs_data, upto_idx):
    """回放 [0, upto_idx)，返回 (拓扑状态, run 命令日志[(epoch下标, 节点, 子命令)])"""
    topo = Topology()
    log = []
    for i in range(upto_idx):
        data = epochs_data[i]
        topo.apply(data, i)
        run = data.get("run")
        if isinstance(run, dict):
            for node, cmds in run.items():
                if not isinstance(cmds, list):
                    continue
                for c in cmds:
                    if isinstance(c, str):
                        for sub in split_cmds(c):
                            log.append((i, node, sub))
    return topo, log


def build_history_table(topo, run_log):
    """
    把被截掉的历史命令整理成 "窗口起点仍然有效的路由表":
      table[(节点, 目的IP)] = {metric: 下一跳节点}   (同一 (节点,目的,metric) 以最后一次为准)
    接口对应的链路已不存在、或命令早于该链路最近一次建立的，视为残留丢弃。
    非路由命令 (无法识别的) 单独返回，保留最后一次出现。
    """
    table, others = {}, {}
    script, suffixes = None, set()
    for pos, (i, node, sub) in enumerate(run_log):
        info = cmd_iface_peer(sub)
        if info:
            pair = tuple(sorted([node, info[0]]))
            if pair not in topo.links or i < topo.since.get(pair, 0):
                continue
        m = ROUTE_RE.match(sub)
        im = re.fullmatch(r'vl_(.+)_(\d+)', m.group(2)) if m else None
        if m and im:
            script = script or m.group(1)
            dest = m.group(3)
            try:
                dest = str(ipaddress.IPv6Address(dest))
            except ValueError:
                pass
            suffixes.add(im.group(2))
            table.setdefault((node, dest), {})[int(m.group(4))] = im.group(1)
        else:
            others[(node, sub)] = pos
    others_by_node = {}
    for (node, sub), _ in sorted(others.items(), key=lambda kv: kv[1]):
        others_by_node.setdefault(node, []).append(sub)
    n_cmds = sum(len(v) for v in table.values())
    return table, others_by_node, script, n_cmds, suffixes


# ---------------------------------------------------------------------------
# 连接模型 -> 选路
# ---------------------------------------------------------------------------
def link_delay_ms(link):
    for k in DELAY_KEYS:
        if k not in link:
            continue
        v = link[k]
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            m = re.fullmatch(r'\s*([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*(us|ms|s)?\s*', v)
            if m:
                x, unit = float(m.group(1)), (m.group(2) or "ms")
                return x / 1000.0 if unit == "us" else x * 1000.0 if unit == "s" else x
    return None


def build_graph(topo, mode):
    """mode: 'hops' | 'delay'。返回 {u: [(v, w), ...]}；delay 模式下有链路没有可解析的时延则返回 None。"""
    graph = {}
    for (a, b), link in topo.links.items():
        if mode == "hops":
            w = 1.0
        else:
            w = link_delay_ms(link)
            if w is None:
                return None
        graph.setdefault(a, []).append((b, w))
        graph.setdefault(b, []).append((a, w))
    for u in graph:
        graph[u].sort(key=lambda e: node_key(e[0]))
    return graph


class RouteModel:
    """某个时刻的连接模型 + 最短路 (地面/用户节点只做端点，不做中转)。"""

    def __init__(self, graph):
        self.graph = graph
        self._cache = {}

    def dist(self, root):
        d = self._cache.get(root)
        if d is None:
            d = {root: 0.0}
            heap = [(0.0, root)]
            done = set()
            while heap:
                du, u = heapq.heappop(heap)
                if u in done:
                    continue
                done.add(u)
                if u != root and not is_satellite(u):
                    continue
                for v, w in self.graph.get(u, ()):
                    nd = du + w
                    if nd < d.get(v, float("inf")):
                        d[v] = nd
                        heapq.heappush(heap, (nd, v))
            self._cache[root] = d
        return d

    def choose(self, S, D, backup_mode):
        """返回 (各邻居经由代价, 主路径(下一跳,代价)|None, 备份(下一跳,代价)|None)"""
        dist_D = self.dist(D)
        costs = {}
        for v, w in self.graph.get(S, ()):
            if v != D and not is_satellite(v):
                continue
            dv = dist_D.get(v)
            if dv is not None:
                costs[v] = w + dv
        if not costs:
            return costs, None, None
        order = sorted(costs.items(), key=lambda kv: (kv[1], node_key(kv[0])))
        prim = order[0]
        backup = None
        dist_S = self.dist(S) if backup_mode == "lfa" else None
        for v, c in order[1:]:
            if backup_mode == "lfa":
                ds = dist_S.get(v)
                if ds is None or not (dist_D[v] < ds + prim[1] - 1e-9):
                    continue
            backup = (v, c)
            break
        return costs, prim, backup


def evaluate(model, backup_mode, pairs, ip_map):
    """拿模型的选路和历史路由表比较 (按代价比较，等价路径不算错)。"""
    n = p_ok = b_ok = 0
    bad = []
    inf = float("inf")
    for (S, dip), entries in pairs:
        D = ip_map.get(dip)
        if D is None or D == S or S not in model.graph or D not in model.graph:
            continue
        costs, prim, backup = model.choose(S, D, backup_mode)
        if prim is None:
            continue
        n += 1
        raw_p, raw_b = entries.get(PRIMARY_METRIC), entries.get(BACKUP_METRIC)
        ok_p = raw_p is not None and abs(costs.get(raw_p, inf) - prim[1]) < 1e-6
        if backup is None:
            ok_b = raw_b is None
        else:
            ok_b = raw_b is not None and abs(costs.get(raw_b, inf) - backup[1]) < 1e-6
        p_ok += ok_p
        b_ok += ok_b
        if not ok_p and len(bad) < 5:
            bad.append((S, D, raw_p, costs.get(raw_p), prim[0], prim[1]))
    return {"n": n, "p": p_ok / n if n else 0.0, "b": b_ok / n if n else 0.0, "bad": bad}


def rewrite_routes(topo0, run_log, target_nodes, dest_scope):
    """
    根据窗口起点的连接模型重新生成路由注入，返回 {节点: [run 字符串, ...]}。
    校验失败 (IP 规则对不上 / 选路规则对不上) 时直接退出，不写任何文件。
    """
    table, others, script, n_hist, suffixes = build_history_table(topo0, run_log)
    script = script or ROUTE_SCRIPT_DEFAULT
    live = sorted([n for n, nb in topo0.adj.items() if nb], key=node_key)

    no_ip = [n for n in live if node_ip(n) is None]
    if no_ip:
        print(f"Warning: no IP rule for node(s) {no_ip[:8]}{'...' if len(no_ip) > 8 else ''}; they get no routes and are never used as destinations.")
    ip_map = {node_ip(n): n for n in live if node_ip(n)}

    attr_keys = sorted({k for l in topo0.links.values() for k in l if k not in ("endpoint1", "endpoint2")})
    print(f"Link attributes seen: {attr_keys}")

    # --- IP 规则校验: 历史路由里出现的所有目的地都应能由规则解释 ---
    dests_seen = {d for (_, d) in table}
    if dests_seen:
        bad_ips = sorted(d for d in dests_seen if decode_ip(d) is None)
        if len(bad_ips) > 0.05 * len(dests_seen):
            print(f"Error: {len(bad_ips)}/{len(dests_seen)} destination addresses in the skipped route commands do not follow the "
                  f"IP rule {IP_PREFIX} + {IP_HOST_BASE} + {IP_HOST_STEP}*(n-1), e.g. {bad_ips[:3]}.")
            print("       Adjust IP_PREFIX / IP_HOST_BASE / IP_HOST_STEP at the top of the script. Nothing was written.")
            sys.exit(2)
        print(f"IP rule check: all {len(dests_seen) - len(bad_ips)} of {len(dests_seen)} destination addresses in the skipped routes decode to a node name.")
    if suffixes - {"1"}:
        print(f"Warning: interface index suffixes {sorted(suffixes)} seen in route commands; the model always uses '_1'.")

    # --- 选路模型: 用历史路由表校验，自动选权重(时延/跳数)和备份规则 ---
    models = {}
    for wm in ("delay", "hops"):
        g = build_graph(topo0, wm)
        if g is not None:
            models[wm] = RouteModel(g)
    if not models:
        return {}

    pairs = sorted(table.items())
    pairs = pairs[::max(1, len(pairs) // VALIDATE_MAX_PAIRS)]
    results = []
    for wm, model in models.items():
        for bm in BACKUP_MODES:
            results.append((wm, bm, evaluate(model, bm, pairs, ip_map)))
    best = None
    for r in results:
        if best is None or (r[2]["p"], r[2]["b"]) > (best[2]["p"], best[2]["b"]):
            best = r

    if best[2]["n"] >= MIN_VALIDATE_PAIRS:
        print(f"Route model check against {n_hist} live history route commands ({best[2]['n']} sampled node/destination pairs):")
        for wm, bm, st in results:
            mark = "  <-- selected" if (wm, bm) == (best[0], best[1]) else ""
            print(f"  weights={wm:<5} backup={bm:<11} primary {st['p']:6.1%}  backup {st['b']:6.1%}{mark}")
        if best[2]["p"] < ROUTE_MATCH_MIN:
            print(f"Error: the best model reproduces only {best[2]['p']:.1%} of the primary routes in the raw data (< {ROUTE_MATCH_MIN:.0%}).")
            for S, D, rp, rc, mp, mc in best[2]["bad"]:
                print(f"       {S}->{D}: raw primary via {rp} (model cost {rc}), model primary via {mp} (cost {mc})")
            print("       The routing rule of the generator differs from the model. Nothing was written.")
            sys.exit(2)
        wm, bm = best[0], best[1]
    else:
        wm = "delay" if "delay" in models else "hops"
        bm = "lfa"
        print(f"Route model not verified (only {best[2]['n']} comparable route entries in the skipped epochs); using weights={wm}, backup={bm}.")

    model = models[wm]

    # --- 目的地范围 ---
    if dest_scope == "all":
        dest_nodes = [n for n in live if node_ip(n)]
    else:
        dest_nodes = [n for n in sorted(target_nodes, key=node_key) if n in model.graph and node_ip(n)]
        absent = [n for n in target_nodes if n not in model.graph]
        if absent:
            print(f"Note: {absent} not connected at the window start, no routes to them in epoch0 (the raw route updates install them later).")

    # --- 生成: 每个 (节点, 目的地) 一条主路径 + 一条备份路径 ---
    by_node, n_new = {}, 0
    for S in sorted(model.graph, key=node_key):
        subs = list(others.get(S, []))
        for D in dest_nodes:
            if D == S or D not in model.graph:
                continue
            _, prim, backup = model.choose(S, D, bm)
            if prim is None:
                continue
            ip = node_ip(D)
            subs.append(f"{script} vl_{prim[0]}_1 {ip} {PRIMARY_METRIC}")
            n_new += 1
            if backup:
                subs.append(f"{script} vl_{backup[0]}_1 {ip} {BACKUP_METRIC}")
                n_new += 1
        if subs:
            by_node[S] = subs
    for S, subs in others.items():       # 没有链路的节点上的非路由命令: 接口过滤时已处理，这里只保留无接口命令
        if S not in by_node:
            by_node[S] = list(subs)

    print(f"Epoch0 routes rewritten from the connection model: {n_new} route commands on {len(by_node)} nodes "
          f"(destinations: {dest_scope}, {len(dest_nodes)} nodes); live history table had {n_hist} commands.")
    if others:
        print(f"Kept {sum(len(v) for v in others.values())} non-route commands from the skipped epochs unchanged.")
    return {node: ["; ".join(subs[j:j + ROUTE_CHUNK]) for j in range(0, len(subs), ROUTE_CHUNK)]
            for node, subs in by_node.items()}


# ---------------------------------------------------------------------------
# 连接段 / 自检
# ---------------------------------------------------------------------------
def find_segments(conn):
    segs, s = [], None
    for i, c in enumerate(conn):
        if c and s is None:
            s = i
        elif not c and s is not None:
            segs.append((s, i - 1))
            s = None
    if s is not None:
        segs.append((s, len(conn) - 1))
    return segs


def verify_output(epochs_data, out_epochs, start_idx, nodes):
    """逐帧回放输出，与原始时间线在对应时刻的链路状态比对。返回 (不一致帧数, 首个不一致帧, 输出异常数, 连通帧数)"""
    orig, out = Topology(), Topology()
    oi = -1
    mismatches, first_bad, connected_epochs = 0, None, 0
    for k, ep in enumerate(out_epochs):
        target = (start_idx - 1 + k) if start_idx > 0 else k
        while oi < target:
            oi += 1
            orig.apply(epochs_data[oi], oi)
        out.apply(ep, k)
        if out.links != orig.links:
            mismatches += 1
            if first_bad is None:
                first_bad = k
        if out.connected(nodes):
            connected_epochs += 1
    return mismatches, first_bad, out.anomalies, connected_epochs


def main():
    parser = argparse.ArgumentParser(description="Extract Continuous Network Usage Subsets with Clean GSL States")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw epochs")
    parser.add_argument("--output-dir", required=True, help="Directory to save extracted epochs")
    parser.add_argument("--nodes", type=str, nargs="+", default=["usr1", "grd7"], help="Nodes to track (default: usr1 grd7)")
    parser.add_argument("--min-duration", type=int, default=3600, help="Minimum continuous network availability in seconds (default: 3600)")
    parser.add_argument("--buffer", type=int, default=180, help="Buffer time to keep before and after in seconds (default: 180)")
    parser.add_argument("--route-dests", choices=["targets", "all"], default="targets",
                        help="Destinations of the epoch0 route injection: only the tracked --nodes (default, smallest) or every node")
    args = parser.parse_args()

    if os.path.abspath(args.input_dir) == os.path.abspath(args.output_dir):
        print("Error: --input-dir and --output-dir must be different (the raw epochs would be overwritten).")
        sys.exit(1)

    epoch_files = glob(os.path.join(args.input_dir, "NetSatBench-epoch*.json"))
    epoch_files.sort(key=get_epoch_number)
    if not epoch_files:
        print(f"Error: No epoch files found in {args.input_dir}")
        return

    print(f"Scanning epochs to find continuous network availability for {args.nodes} >= {args.min_duration}s...")

    # 1. 读取并逐帧判断连通性
    epochs_data, times, conn = [], [], []
    topo = Topology()
    for idx, path in enumerate(epoch_files):
        with open(path, 'r') as f:
            data = json.load(f)
        epochs_data.append(data)
        times.append(parse_time(data["time"]))
        topo.apply(data, idx)
        conn.append(topo.connected(args.nodes))

    if any(times[i] < times[i - 1] for i in range(1, len(times))):
        print("Warning: epoch timestamps are not monotonically increasing; check the raw data.")
    if topo.anomalies:
        print(f"Warning: {topo.anomalies} inconsistent link events in the raw epochs "
              f"(delete/update of a missing link, or duplicate add).")
    missing = [n for n in args.nodes if n not in topo.ever_nodes]
    if missing:
        print(f"Error: node(s) {missing} never appear in any link of the raw epochs.")
        return

    # 2. 连接段 + 选出第一个满足时长的长连接
    segs = find_segments(conn)

    def seg_dur(sg):
        return (times[sg[1]] - times[sg[0]]).total_seconds()

    def gap(a, b):
        return (times[b[0]] - times[a[1]]).total_seconds()

    target_k = next((k for k, sg in enumerate(segs) if seg_dur(sg) >= args.min_duration), None)
    if target_k is None:
        longest = max((seg_dur(sg) for sg in segs), default=0)
        print(f"No continuous network availability found exceeding {args.min_duration} seconds "
              f"(longest end-to-end connection: {longest:.0f} s).")
        return

    # 3. 紧挨着的短连接一并保留: 相邻两段之间的间隔 <= buffer 就并入
    lo = hi = target_k
    while lo > 0 and gap(segs[lo - 1], segs[lo]) <= args.buffer:
        lo -= 1
    while hi < len(segs) - 1 and gap(segs[hi], segs[hi + 1]) <= args.buffer:
        hi += 1

    long_seg = segs[target_k]
    kept_short = [sg for k, sg in enumerate(segs[lo:hi + 1], start=lo) if k != target_k]
    cluster_start, cluster_end = times[segs[lo][0]], times[segs[hi][1]]

    # 4. 前后各留 buffer 秒无连接状态
    window_start_time = cluster_start - timedelta(seconds=args.buffer)
    window_end_time = cluster_end + timedelta(seconds=args.buffer)

    start_idx = next(i for i, t in enumerate(times) if t >= window_start_time)
    end_idx = max(i for i, t in enumerate(times) if t <= window_end_time)

    print(f"\n--- FOUND CONTINUOUS NETWORK AVAILABILITY ---")
    print(f"Nodes: {args.nodes}")
    print(f"Duration: {seg_dur(long_seg)} seconds")
    print(f"Long connection: epoch idx {long_seg[0]}-{long_seg[1]} ({times[long_seg[0]]} to {times[long_seg[1]]})")
    if kept_short:
        print(f"Adjacent short connections kept ({len(kept_short)}): " +
              ", ".join(f"idx {a}-{b} ({seg_dur((a, b)):.0f}s)" for a, b in kept_short))
    else:
        print("Adjacent short connections kept: none")
    print(f"Base Time: {cluster_start} to {cluster_end}")
    print(f"Extraction Window (with {args.buffer}s buffer): {window_start_time} to {window_end_time}")
    print(f"Extracting original epochs from index {start_idx} to {end_idx}")

    # 5. 构造输出: [epoch0 快照] + 窗口内原始 epoch (原样)
    out_epochs = []
    if start_idx > 0:
        topo0, run_log = replay_prefix(epochs_data, start_idx)
        run0 = rewrite_routes(topo0, run_log, args.nodes, args.route_dests)

        snapshot = {
            "time": epochs_data[start_idx - 1]["time"],
            "links-del": [],
            "links-update": [],
            "links-add": [copy.deepcopy(topo0.links[p]) for p in sorted(topo0.links)],
        }
        if run0 or "run" in epochs_data[start_idx - 1]:
            snapshot["run"] = run0
        out_epochs.append(snapshot)

        print(f"Epoch0 Snapshot: {len(snapshot['links-add'])} live links at {snapshot['time']}.")
        extra_keys = set().union(*[set(e.keys()) for e in epochs_data[:start_idx]]) - KNOWN_KEYS
        if extra_keys:
            print(f"Warning: skipped epochs contain other fields {sorted(extra_keys)}; they are not carried into epoch0.")

    for i in range(start_idx, end_idx + 1):
        out_epochs.append(copy.deepcopy(epochs_data[i]))

    # 写文件前清掉输出目录里旧的 epoch 文件，避免残留
    os.makedirs(args.output_dir, exist_ok=True)
    stale = glob(os.path.join(args.output_dir, "NetSatBench-epoch*.json"))
    for p in stale:
        os.remove(p)
    if stale:
        print(f"Removed {len(stale)} stale epoch files from {args.output_dir}")

    for k, ep in enumerate(out_epochs):
        with open(os.path.join(args.output_dir, f"NetSatBench-epoch{k}.json"), 'w') as f:
            json.dump(ep, f, indent=2)

    # 6. 自检
    mism, first_bad, out_anom, conn_epochs = verify_output(epochs_data, out_epochs, start_idx, args.nodes)
    if mism == 0:
        print("Consistency check: link state of the subset matches the raw timeline on every epoch.")
    else:
        print(f"Consistency check FAILED: {mism} epochs differ from the raw timeline (first at epoch{first_bad}).")
    if out_anom:
        print(f"Note: {out_anom} inconsistent link events in the subset (same as in the raw data).")

    total_sec = (parse_time(out_epochs[-1]["time"]) - parse_time(out_epochs[0]["time"])).total_seconds()
    ep0_kb = os.path.getsize(os.path.join(args.output_dir, "NetSatBench-epoch0.json")) / 1024
    print("\n--- EXTRACTION COMPLETE ---")
    print(f"Total Sub-Epochs Generated: {len(out_epochs)}")
    print(f"Total Duration: {total_sec:.0f} seconds ({timedelta(seconds=int(total_sec))})")
    print(f"Epochs with end-to-end connectivity: {conn_epochs}/{len(out_epochs)}")
    print(f"Epoch0 file size: {ep0_kb:.1f} KB")
    print(f"Output Directory: {args.output_dir}\n")


if __name__ == "__main__":
    main()