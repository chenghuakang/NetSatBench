#!/usr/bin/env python3
import os
import sys
import re
import time
import json
import subprocess
import signal
import shutil
import glob
from datetime import datetime, timedelta

# ================= 配置区 =================
POLICIES = [
    "random_handover",
    "minimum_lifetime",
    "min_orbit_hops",
    "min_full_delay",
    "min_access_delay",
    "max-min-visibility",
    "max-access-rate"
]

RUNS_PER_POLICY = 5
CONFIG_FILE = "./examples/StarPerf/OneWebSmall/sat-config.json"
RESULTS_DIR = "./uncleaned_results"
BASE_EPOCH_PATH = "examples/StarPerf/OneWebSmall/epochs-or-delay-v6-{}-sub"
CHECKPOINT_FILE = os.path.join(RESULTS_DIR, "checkpoint.json")
# ==========================================

def get_epoch_number(filepath):
    match = re.search(r'epoch(\d+)', os.path.basename(filepath))
    return int(match.group(1)) if match else -1

def parse_time(time_str: str) -> datetime:
    return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%SZ")

def format_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return []

def save_checkpoint(completed_runs):
    temp_file = CHECKPOINT_FILE + ".tmp"
    with open(temp_file, 'w') as f:
        json.dump(completed_runs, f, indent=4)
    os.replace(temp_file, CHECKPOINT_FILE)

def update_config(target_dir):
    with open(CONFIG_FILE, 'r') as f:
        content = f.read()
    
    new_content = re.sub(
        r'("epoch-dir"\s*:\s*")[^"]+(")',
        f'\\g<1>{target_dir}\\g<2>',
        content
    )
    
    with open(CONFIG_FILE, 'w') as f:
        f.write(new_content)
    print(f"[Config] 修改 epoch-dir -> {target_dir}")

def run_cmd_with_input(cmd, strict=False):
    print(f"[Exec] {cmd}")
    auto_input = "yes\n" * 5 if "rm " in cmd else "y\n" * 5
    result = subprocess.run(cmd, shell=True, input=auto_input, text=True)
    
    if result.returncode != 0:
        msg = f"'{cmd}' 退出码异常 ({result.returncode})"
        if strict:
            print(f"\n[Error] 严重失败: {msg}")
            return False
        else:
            print(f"\n[Warning] {msg}，非核心步骤，脚本将继续执行...")
    return True

def run_full_pipeline_dry_run():
    print("="*60)
    print(" 正在执行全链路仿真预检 (抽取稳定连通段100个Epoch, 1s极速推进)...")
    print("="*60)
    
    os.makedirs(RESULTS_DIR, exist_ok=True)
    
    source_policy = POLICIES[0]
    source_dir = BASE_EPOCH_PATH.format(source_policy)
    test_dir = "./examples/StarPerf/OneWebSmall/test-epochs-100s"

    if not os.path.exists(source_dir):
        print(f" [✗] 严重错误: 找不到源数据目录 {source_dir}")
        sys.exit(1)

    os.makedirs(test_dir, exist_ok=True)
    for f in glob.glob(os.path.join(test_dir, "*.json")):
        os.remove(f)

    all_epochs = sorted(glob.glob(os.path.join(source_dir, "NetSatBench-epoch*.json")), key=get_epoch_number)
    if len(all_epochs) < 350:
        print(f" [✗] 严重错误: 数据量不足，无法避开开头的断连缓冲期！")
        sys.exit(1)

    # 跳过前 200 帧（彻底避开断连期），抽取 100 帧
    start_offset = 200
    test_len = 100
    
    # 1. 聚合前 200 帧的链路状态，生成精准的背景拓扑快照
    active_links = {}
    for i in range(start_offset):
        with open(all_epochs[i], 'r') as f:
            data = json.load(f)
        for link in data.get("links-del", []):
            pair = tuple(sorted([link["endpoint1"], link["endpoint2"]]))
            active_links.pop(pair, None)
        for link in data.get("links-add", []):
            pair = tuple(sorted([link["endpoint1"], link["endpoint2"]]))
            active_links[pair] = link.copy()
        for link in data.get("links-update", []):
            pair = tuple(sorted([link["endpoint1"], link["endpoint2"]]))
            if pair in active_links:
                active_links[pair].update(link)

    # 2. 生成测试集 epoch0
    with open(all_epochs[start_offset], 'r') as f:
        base_data = json.load(f)
    base_time = parse_time(base_data["time"])
    
    test_epoch0 = {
        "time": format_time(base_time - timedelta(seconds=1)),
        "links-del": [],
        "links-update": [],
        "links-add": list(active_links.values())
    }
    with open(os.path.join(test_dir, "NetSatBench-epoch0.json"), 'w') as f:
        json.dump(test_epoch0, f, indent=2)

    # 3. 抽取后 100 帧，时间戳严格改写为间隔 1 秒
    sample_epochs = all_epochs[start_offset : start_offset + test_len]
    for idx, ep_path in enumerate(sample_epochs, start=1):
        with open(ep_path, 'r') as f:
            ep_data = json.load(f)
        
        new_time = base_time + timedelta(seconds=(idx - 1))
        ep_data["time"] = format_time(new_time)
        
        with open(os.path.join(test_dir, f"NetSatBench-epoch{idx}.json"), 'w') as f:
            json.dump(ep_data, f, indent=2)

    print(f" [✓] 成功聚合前 {start_offset} 帧历史拓扑，并抽取 {test_len} 帧改写为 1s 间隔 -> {test_dir}")

    update_config(test_dir)
    
    print(" [Run] 部署并启动极速仿真...")
    run_cmd_with_input("python3 ./nsb.py rm -t 4", strict=False)
    run_cmd_with_input("python3 ./nsb.py system-clean-docker", strict=False)
    run_cmd_with_input("python3 ./nsb.py system-init-docker --config ./examples/10nodes/worker-config.json", strict=True)
    run_cmd_with_input("python3 ./nsb.py init --config ./examples/StarPerf/OneWebSmall/sat-config.json", strict=True)
    run_cmd_with_input("python3 ./nsb.py deploy -t 4", strict=True)
    
    test_trace_file = os.path.join(RESULTS_DIR, "test_traceroute_output.txt")
    if os.path.exists(test_trace_file):
        os.remove(test_trace_file)
        
    trace_script = (
        f"while true; do "
        f"echo '=== Test at $(date) ==='; "
        f"python3 ./nsb.py exec usr1 traceroute -n grd7; "
        f"echo -e '\\n\\n'; "
        f"sleep 1; "
        f"done > {test_trace_file} 2>&1"
    )
    trace_proc = subprocess.Popen(trace_script, shell=True, executable='/bin/bash', preexec_fn=os.setsid)
    
    run_success = run_cmd_with_input("python3 ./nsb.py run", strict=True)
    
    try:
        os.killpg(os.getpgid(trace_proc.pid), signal.SIGTERM)
        trace_proc.wait(timeout=5)
    except Exception:
        pass

    if not run_success:
        print(" [✗] 严重错误: 极速仿真运行进程异常退出！")
        sys.exit(1)

    if not os.path.exists(test_trace_file) or os.path.getsize(test_trace_file) < 20:
        print(" [✗] 严重错误: Traceroute 输出文件未生成或内容过少！")
        sys.exit(1)
        
    with open(test_trace_file, 'r') as f:
        trace_content = f.read()
        if "ms" not in trace_content:
            print(" [✗] 严重警告: Traceroute 结果中未检测到有效延迟回包 ('ms')！截取输出内容如下：")
            print(trace_content[-800:]) 
            sys.exit(1)
            
    print(" [✓] Traceroute 连通性探活完美通过！前100帧中成功检测到了真实网络 RTT 回包。")
    
    run_cmd_with_input("python3 ./nsb.py rm -t 4", strict=False)
    run_cmd_with_input("python3 ./nsb.py system-clean-docker", strict=False)
    
    print("="*60)
    print(" 预检全链路测试 100% 成功！3秒后开始正式评估长跑...\n")
    time.sleep(3)


def main():
    os.environ["ETCD_HOST"] = "192.168.248.30"
    os.environ["ETCD_PORT"] = "2379"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1,192.168.236.0/24"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,192.168.236.0/24"
    print("[Env] 环境变量已加载.\n")

    run_full_pipeline_dry_run()

    completed_runs = load_checkpoint()
    if completed_runs:
        print(f"[Checkpoint] 检测到已完成 {len(completed_runs)} 个任务，从断点继续...")

    for policy in POLICIES:
        policy_fully_done = all(f"{policy}_{i}" in completed_runs for i in range(1, RUNS_PER_POLICY + 1))
        if policy_fully_done:
            print(f"\n[Skip] 策略 {policy} 的所有 {RUNS_PER_POLICY} 轮测试已完成，跳过。")
            continue

        print(f"\n{'#'*60}")
        print(f" 评估策略: {policy}")
        print(f"{'#'*60}")
        
        target_policy_dir = BASE_EPOCH_PATH.format(policy)
        update_config(target_policy_dir)

        for run_id in range(1, RUNS_PER_POLICY + 1):
            run_key = f"{policy}_{run_id}"
            if run_key in completed_runs:
                continue

            print(f"\n--- [ {policy} ] 第 {run_id}/{RUNS_PER_POLICY} 次测试 ---")
            
            run_cmd_with_input("python3 ./nsb.py rm -t 4", strict=False)
            run_cmd_with_input("python3 ./nsb.py system-clean-docker", strict=False)
            run_cmd_with_input("python3 ./nsb.py system-init-docker --config ./examples/10nodes/worker-config.json", strict=True)
            run_cmd_with_input("python3 ./nsb.py init --config ./examples/StarPerf/OneWebSmall/sat-config.json", strict=True)
            run_cmd_with_input("python3 ./nsb.py deploy -t 4", strict=True)
            
            trace_file = os.path.join(RESULTS_DIR, f"traceroute_{policy}_{run_id}.txt")
            trace_script = (
                f"while true; do "
                f"echo '=== Test at $(date) ==='; "
                f"python3 ./nsb.py exec usr1 traceroute -n grd7; "
                f"echo -e '\\n\\n'; "
                f"sleep 1; "
                f"done > {trace_file} 2>&1"
            )
            print(f"[Trace] 启动后台测试 -> {trace_file}")
            trace_proc = subprocess.Popen(trace_script, shell=True, executable='/bin/bash', preexec_fn=os.setsid)
            
            print(f"[Run] 开始主仿真任务 run ...")
            success = run_cmd_with_input("python3 ./nsb.py run", strict=True)
            
            print(f"[Trace] 仿真结束，清理后台 traceroute 脚本...")
            try:
                os.killpg(os.getpgid(trace_proc.pid), signal.SIGTERM)
                trace_proc.wait(timeout=5)
            except Exception:
                pass 

            if success:
                completed_runs.append(run_key)
                save_checkpoint(completed_runs)
                print(f"[Checkpoint] 保存存档点: {run_key}")
            else:
                print(f"\n[Fatal] {run_key} 仿真失败！批处理终止，下次运行将自动重试本轮。")
                sys.exit(1)

    print("\n" + "="*60)
    print(" 所有任务全部圆满完成！数据保存在 ./uncleaned_results/")
    print("="*60)

if __name__ == '__main__':
    main()