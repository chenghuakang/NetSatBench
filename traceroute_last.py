from datetime import datetime
import re
import matplotlib.pyplot as plt

# =====================================================================
# 1. 智能解析 traceroute_20min.txt 文件 (动态提取倒数第二跳)
# =====================================================================
file_path = "traceroute_OneWebSmall_200min_4.txt"

data_blocks = [] # 存储所有的测试块数据
current_block = {"timestamp": None, "hops": []}

with open(file_path, "r", encoding="utf-8") as f:
    for line in f:
        # 匹配时间戳行
        time_match = re.search(r"=== Test at (.+?) ===", line)
        if time_match:
            # 如果上一个区块有数据，先存起来
            if current_block["timestamp"] is not None and current_block["hops"]:
                data_blocks.append(current_block)
            
            # 初始化新区块
            time_str = time_match.group(1)
            try:
                cleaned_time_str = re.sub(r"\b[A-Z]{3,4}\b", "", time_str).strip()
                cleaned_time_str = " ".join(cleaned_time_str.split())
                dt = datetime.strptime(cleaned_time_str, "%a %b %d %H:%M:%S %Y")
                current_block = {"timestamp": dt, "hops": []}
            except Exception:
                current_block = {"timestamp": None, "hops": []}
            continue

        # 匹配任意跳数的行 (形如: 1  172.20.0.1 ... 或 5  172.20.100.6 ...)
        hop_match = re.match(r"^\s*(\d+)\s+", line)
        if current_block["timestamp"] is not None and hop_match:
            # 提取这一行中的 IP 地址
            ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
            if ip_match:
                current_block["hops"].append(ip_match.group(0))
            elif "*" in line:
                current_block["hops"].append("Unknown/Dropped")

# 把最后一个区块放进去
if current_block["timestamp"] is not None and current_block["hops"]:
    data_blocks.append(current_block)

# =====================================================================
# 2. 动态提取“倒数第二跳”并计算相对时间
# =====================================================================
timestamps = []
penultimate_hop_ips = []
start_time = None
last_valid_ip = "Unknown/Dropped"

if data_blocks:
    start_time = data_blocks[0]["timestamp"]

for block in data_blocks:
    relative_seconds = (block["timestamp"] - start_time).total_seconds()
    hops_list = block["hops"]
    
    # 核心筛选：至少需要有 2 跳（网关 + 目的地），我们才能取倒数第二跳
    if len(hops_list) >= 2:
        # 倒数第一是 hops_list[-1] (通常是 grd1)，倒数第二是 hops_list[-2]
        target_ip = hops_list[-2]
        if target_ip != "Unknown/Dropped":
            last_valid_ip = target_ip
    else:
        # 如果当前测试块由于断连严重，连 2 跳都没凑齐，则标记为丢失或沿用
        target_ip = "Unknown/Dropped"
        
    timestamps.append(relative_seconds)
    penultimate_hop_ips.append(target_ip)

# =====================================================================
# 3. 将 IP 映射为节点名称 (沿用您的网络拓扑映射)
# =====================================================================
def get_node_name(ip):
    if ip == "Unknown/Dropped":
        return "Unknown/Dropped"
    match = re.match(r"(\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d{1,3})", ip)
    if not match: return f"Unknown ({ip})"
    
    prefix, last_octet = match.group(1), int(match.group(2))
    if prefix == "172.20.101": return f"grd1 ({ip})"
    if prefix == "172.20.102": return f"usr1 ({ip})"
    if prefix == "172.20.100":
        sat_id = (last_octet // 4) + 1
        if sat_id >= 11: sat_id += 1 # 修正 sat11 跳过断档
        return f"sat{sat_id} ({ip})"
    return f"Node ({ip})"

unique_ips = sorted(list(set(penultimate_hop_ips)))
if "Unknown/Dropped" in unique_ips:
    unique_ips.remove("Unknown/Dropped")
    unique_ips.insert(0, "Unknown/Dropped")

ip_to_id = {ip: idx for idx, ip in enumerate(unique_ips)}
y_axis_labels = [get_node_name(ip) for ip in unique_ips]
node_ids = [ip_to_id[ip] for ip in penultimate_hop_ips]

print(f"成功解析了 {len(timestamps)} 组测试数据。")
print(f"识别到的【倒数第二跳】节点链路: {y_axis_labels}")

# =====================================================================
# 4. 绘制倒数第二跳的阶梯图
# =====================================================================
if not timestamps:
    print("错误：未解析到有效数据！")
else:
    plt.figure(figsize=(14, 7), dpi=100)

    plt.step(timestamps, node_ids, where='post', color='#e377c2', linewidth=2.5, label='Penultimate Hop')
    plt.plot(timestamps, node_ids, 'ro', markersize=4, label='Check Point')

    plt.title('Penultimate Hop Node (Last Satellite before Destination) vs. Time', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Relative Time (seconds)', fontsize=12)
    plt.ylabel('Active Penultimate Node / Satellite', fontsize=12)
    
    plt.yticks(range(len(unique_ips)), y_axis_labels)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend(loc='upper right')
    plt.tight_layout()

    plt.savefig('handover_penultimate_hop.png')
    plt.show()