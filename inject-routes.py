#!/usr/bin/env python3
import json
import subprocess
import glob
import os
import re
import time

epoch_dir = "./examples/StarPerf/OneWebSmall/epochs-max-min-visibility"
epoch_files = sorted(
    glob.glob(os.path.join(epoch_dir, "NetSatBench-epoch*.json")), 
    key=lambda x: int(re.findall(r"(\d+)", os.path.basename(x))[-1])
)

print(f"🚀 Starting Dynamic Route Injector for {len(epoch_files)} epochs...")

for path in epoch_files:
    print(f"⏱️ Applying dynamic routes from {os.path.basename(path)}...")
    with open(path, "r") as f:
        data = json.load(f)
    
    # 1. 顺便模拟网卡创建时间，先让链路加进去
    time.sleep(1)
    
    run_cmds = data.get("run", {})
    for node_name, cmds in run_cmds.items():
        if not cmds: 
            continue
        
        # 逐条执行该节点在当前 epoch 的所有 ip route 命令
        # cmds[0] 里面用分号隔开多条命令
        full_cmd_string = cmds[0].replace("sleep 2; ", "")
        
        for single_cmd in full_cmd_string.split(";"):
            single_cmd = single_cmd.strip()
            if not single_cmd: continue
            
            wrapped_cmd = f"python3 ./nsb.py exec {node_name} '{single_cmd}'"
            # 运行并静默输出
            subprocess.run(wrapped_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 保持当前 epoch 状态 5 秒再进入下一个 epoch
    time.sleep(5)

print("🎉 All dynamic epochs injected successfully!")