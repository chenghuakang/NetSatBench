from datetime import datetime
import re
import matplotlib.pyplot as plt

file_path = "traceroute_OneWebSmall_200min_4.txt"

timestamps = []
first_hop_ips = []  # 变量名已修改：记录第一跳
start_time = None
last_valid_ip = "Unknown/Dropped"  # 用于处理 * * * 的初始值

with open(file_path, "r", encoding="utf-8") as f:
    current_time = None
    for line in f:
        # 1. 匹配时间戳行
        time_match = re.search(r"=== Test at (.+?) ===", line)
        if time_match:
            time_str = time_match.group(1)
            try:
                cleaned_time_str = re.sub(r"\b[A-Z]{3,4}\b", "", time_str).strip()
                cleaned_time_str = " ".join(cleaned_time_str.split())
                dt = datetime.strptime(cleaned_time_str, "%a %b %d %H:%M:%S %Y")
                
                if start_time is None:
                    start_time = dt
                
                current_time = (dt - start_time).total_seconds()
            except Exception as e:
                current_time = None
            continue

        # 2. 匹配第一跳 (核心修改：正则表达式匹配以数字 1 开头的行)
        if current_time is not None and re.match(r"^\s*1\s+", line):
            if "*" in line and not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
                current_ip = "Unknown/Dropped"
            else:
                ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
                if ip_match:
                    current_ip = ip_match.group(0)
                    last_valid_ip = current_ip
                else:
                    current_ip = "Unknown/Dropped"
            
            timestamps.append(current_time)
            first_hop_ips.append(current_ip)
            current_time = None

# =====================================================================
# 2. 核心新增：基于 CIDR 分配日志，将 IP 映射为节点名称
# =====================================================================
def get_node_name(ip):
    if ip == "Unknown/Dropped":
        return "Unknown/Dropped"
    
    # 提取 IP 的前三段以做网段匹配 (例如 "172.20.100.14" -> "172.20.100", 尾数 14)
    match = re.match(r"(\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d{1,3})", ip)
    if not match:
        return f"Unknown ({ip})"
    
    prefix, last_octet = match.group(1), int(match.group(2))
    
    # 特殊网段优先匹配：grd1 (172.20.101.0/30) 和 usr1 (172.20.102.0/30)
    if prefix == "172.20.101":
        return f"grd1 ({ip})"
    if prefix == "172.20.102":
        return f"usr1 ({ip})"
    
    # 卫星网段匹配：172.20.100.X/30
    if prefix == "172.20.100":
        # 每个卫星占 4 个 IP (ID = 尾数 // 4 + 1)
        # 例如: .0~.3 是 sat1, .4~.7 是 sat2, .12~.15 是 sat4 ...
        sat_id = (last_octet // 4) + 1
        # 日志中跳过了 sat11，如果是 40 以上需要对应日志转换
        if sat_id >= 11:
            sat_id += 1 # 修正 sat10(.36/30) 后面直接接 sat12(.40/30) 的断档
        return f"sat{sat_id} ({ip})"
        
    return f"Node ({ip})"

# 生成包含节点名称的直观标签
unique_ips = sorted(list(set(first_hop_ips)))
if "Unknown/Dropped" in unique_ips:
    unique_ips.remove("Unknown/Dropped")
    unique_ips.insert(0, "Unknown/Dropped")

# 创建 IP 到 Y 轴编号以及 IP 到"节点展示名称"的映射
ip_to_id = {ip: idx for idx, ip in enumerate(unique_ips)}
y_axis_labels = [get_node_name(ip) for ip in unique_ips]
node_ids = [ip_to_id[ip] for ip in first_hop_ips]

print(f"成功解析了 {len(timestamps)} 组测试数据。")
print(f"识别到的第一跳节点链路: {y_axis_labels}")  # 文本提示已修改

# =====================================================================
# 3. 绘制阶梯图
# =====================================================================
if not timestamps:
    print("错误：未从文件中解析到有效数据！")
else:
    plt.figure(figsize=(14, 7), dpi=100)

    # 画阶梯图 (修改了图例 label)
    plt.step(timestamps, node_ids, where='post', color='#007acc', linewidth=2.5, label='Active First Hop')
    
    # 标记检测点
    plt.plot(timestamps, node_ids, 'ro', markersize=4, label='Check Point')

    # 修改了图表标题
    plt.title('First Hop Handover Topology vs. Time', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Relative Time (seconds)', fontsize=12)
    plt.ylabel('Active Handover Node / Satellite', fontsize=12)
    
    # 核心修改：把 Y 轴刻度替换为 “节点名 (IP)”
    plt.yticks(range(len(unique_ips)), y_axis_labels)
    
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend(loc='upper right')
    plt.tight_layout()

    # 导出文件名也顺带修改了，避免覆盖掉你之前的第二跳图像
    plt.savefig('handover_first_hop_nodes.png')
    plt.show()