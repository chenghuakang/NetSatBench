from datetime import datetime
import re
import matplotlib.pyplot as plt
import ipaddress  # 引入 Python 官方的 IP 处理库

# =====================================================================
# 1. 智能解析 traceroute_20min.txt 文件 (提取第二跳/第二个节点)
# =====================================================================
file_path = "traceroute_20min.txt"

data_blocks = [] 
current_block = {"timestamp": None, "hops": []}

# 提取行内可能的 IPv6 字符串（放宽条件，只要有多个冒号和字母数字就抓）
def extract_ipv6_from_line(line):
    words = line.split()
    for word in words:
        clean_word = word.strip("()")
        if ":" in clean_word and clean_word.count(":") >= 2:
            try:
                ip_obj = ipaddress.IPv6Address(clean_word)
                return str(ip_obj.compressed).lower()
            except ipaddress.AddressValueError:
                continue
    return None

try:
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            time_match = re.search(r"=== Test at (.+?) ===", line)
            if time_match:
                if current_block["timestamp"] is not None and current_block["hops"]:
                    data_blocks.append(current_block)
                time_str = time_match.group(1)
                try:
                    cleaned_time_str = re.sub(r"\b[A-Z]{3,4}\b", "", time_str).strip()
                    cleaned_time_str = " ".join(cleaned_time_str.split())
                    dt = datetime.strptime(cleaned_time_str, "%a %b %d %H:%M:%S %Y")
                    current_block = {"timestamp": dt, "hops": []}
                except Exception:
                    current_block = {"timestamp": None, "hops": []}
                continue

            # 匹配以数字开头的跳数行
            hop_match = re.match(r"^\s*(\d+)\s+", line)
            if current_block["timestamp"] is not None and hop_match:
                extracted_ip = extract_ipv6_from_line(line)
                if extracted_ip:
                    current_block["hops"].append(extracted_ip)
                elif "*" in line:
                    current_block["hops"].append("Unknown/Dropped")

    # 收尾最后一个块
    if current_block["timestamp"] is not None and current_block["hops"]:
        data_blocks.append(current_block)
except FileNotFoundError:
    print(f"错误：在当前目录下未找到日志文件 '{file_path}'！")
    exit()

# =====================================================================
# 2. 动态提取“第二跳”并计算相对时间
# =====================================================================
timestamps = []
second_hop_ips = []
start_time = None

if data_blocks:
    start_time = data_blocks[0]["timestamp"]

for block in data_blocks:
    relative_seconds = (block["timestamp"] - start_time).total_seconds()
    hops_list = block["hops"]
    
    # 核心修改点：提取第二个节点 (索引为 1)
    # 必须保证本次追踪至少记录了 2 跳数据
    if len(hops_list) >= 2:
        target_ip = hops_list[1]  # 提取第二跳
    else:
        target_ip = "Unknown/Dropped"
        
    timestamps.append(relative_seconds)
    second_hop_ips.append(target_ip)

# =====================================================================
# 3. 将 IP 映射为节点名称 (使用最稳健的解析)
# =====================================================================
def get_node_name(ip):
    if ip == "Unknown/Dropped":
        return "Unknown/Dropped"
    
    try:
        # 将 IP 展开为完整的 32 位十六进制形式，防止缩写导致解析失败
        full_ip = ipaddress.IPv6Address(ip).exploded
        parts = full_ip.split(":")
        
        # 提取网段标识 (第三段，比如 0100, 0101, 0102)
        segment = parts[2].lstrip("0") 
        if not segment: segment = "0"
        
        # 提取最后一段
        last_part_hex = parts[7]
        last_octet = int(last_part_hex, 16)
            
        # 映射逻辑
        if segment == "100":
            sat_id = (last_octet // 4) + 1
            return f"sat{sat_id} ({ip})"
        elif segment == "101":
            grd_id = (last_octet // 4) + 1
            return f"grd{grd_id} ({ip})"
        elif segment == "102":
            usr_id = (last_octet // 4) + 1
            return f"usr{usr_id} ({ip})"
    except Exception as e:
        pass
        
    return f"Node ({ip})"

# 生成包含节点名称的标签
unique_ips = sorted(list(set(second_hop_ips)))
if "Unknown/Dropped" in unique_ips:
    unique_ips.remove("Unknown/Dropped")
    unique_ips.insert(0, "Unknown/Dropped")

ip_to_id = {ip: idx for idx, ip in enumerate(unique_ips)}
y_axis_labels = [get_node_name(ip) for ip in unique_ips]
node_ids = [ip_to_id[ip] for ip in second_hop_ips]

print(f"成功解析了 {len(timestamps)} 组测试数据。")
print(f"解析成功的第二跳节点标签: {y_axis_labels}")

# =====================================================================
# 4. 绘制第二跳的阶梯图
# =====================================================================
if not timestamps:
    print("错误：未解析到有效数据！")
else:
    plt.figure(figsize=(14, 7), dpi=100)
    plt.step(timestamps, node_ids, where='post', color='#007acc', linewidth=2.5, label='Second Hop (Ingress Node)')
    plt.plot(timestamps, node_ids, 'ro', markersize=4, label='Check Point')
    plt.title('Second Hop Node vs. Time (IPv6 Complete Fix)', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Relative Time (seconds)', fontsize=12)
    plt.ylabel('Active Second Node / Satellite', fontsize=12)
    plt.yticks(range(len(unique_ips)), y_axis_labels)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend(loc='upper right')
    plt.tight_layout()
    plt.savefig('handover_second_hop.png')
    print("图像已成功保存为 'handover_second_hop.png'")
    plt.show()