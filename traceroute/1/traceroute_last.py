from datetime import datetime
import re
import matplotlib.pyplot as plt
import ipaddress  # 引入 Python 官方的 IP 处理库

# =====================================================================
# 1. 智能解析 traceroute_20min.txt 文件 (动态提取倒数第二跳)
# =====================================================================
file_path = "traceroute_20min.txt"

data_blocks = [] 
current_block = {"timestamp": None, "hops": []}

# 提取行内可能的 IPv6 字符串（放宽条件，只要有多个冒号和字母数字就抓）
def extract_ipv6_from_line(line):
    # 按照空白字符切割，寻找长得像 IPv6 的块
    words = line.split()
    for word in words:
        # 去掉可能的括号，比如 (2001:db8:100::5f)
        clean_word = word.strip("()")
        if ":" in clean_word and clean_word.count(":") >= 2:
            try:
                # 让专业的库来验证它是不是一个合法的 IPv6
                ip_obj = ipaddress.IPv6Address(clean_word)
                # 将其标准化为全小写的压缩格式（例如 2001:db8:100::57）
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
# 2. 动态提取“倒数第二跳”并计算相对时间
# =====================================================================
timestamps = []
penultimate_hop_ips = []
start_time = None

if data_blocks:
    start_time = data_blocks[0]["timestamp"]

for block in data_blocks:
    relative_seconds = (block["timestamp"] - start_time).total_seconds()
    hops_list = block["hops"]
    
    if len(hops_list) >= 2:
        target_ip = hops_list[-2]
    else:
        target_ip = "Unknown/Dropped"
        
    timestamps.append(relative_seconds)
    penultimate_hop_ips.append(target_ip)

# =====================================================================
# 3. 将 IP 映射为节点名称 (使用最稳健的解析)
# =====================================================================
def get_node_name(ip):
    if ip == "Unknown/Dropped":
        return "Unknown/Dropped"
    
    try:
        # 将 IP 展开为完整的 32 位十六进制形式 (无缩写)，方便绝对精准切割
        # 比如 2001:db8:100::5f 会变成 2001:0db8:0100:0000:0000:0000:0000:005f
        full_ip = ipaddress.IPv6Address(ip).exploded
        parts = full_ip.split(":")
        
        # 提取网段标识 (第三段，比如 0100, 0101, 0102)
        segment = parts[2].lstrip("0") # 去掉前导 0，变成 100
        if not segment: segment = "0"
        
        # 提取最后一段 (第八段，比如 005f)
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
        print(f"解析 IP 出错: {ip}, 报错: {e}")
        pass
        
    return f"Node ({ip})"

# 生成包含节点名称的标签
unique_ips = sorted(list(set(penultimate_hop_ips)))
if "Unknown/Dropped" in unique_ips:
    unique_ips.remove("Unknown/Dropped")
    unique_ips.insert(0, "Unknown/Dropped")

ip_to_id = {ip: idx for idx, ip in enumerate(unique_ips)}
y_axis_labels = [get_node_name(ip) for ip in unique_ips]
node_ids = [ip_to_id[ip] for ip in penultimate_hop_ips]

print(f"成功解析了 {len(timestamps)} 组测试数据。")
print(f"解析成功的节点展示标签: {y_axis_labels}")

# =====================================================================
# 4. 绘制倒数第二跳的阶梯图
# =====================================================================
if not timestamps:
    print("错误：未解析到有效数据！")
else:
    plt.figure(figsize=(14, 7), dpi=100)
    plt.step(timestamps, node_ids, where='post', color='#e377c2', linewidth=2.5, label='Penultimate Hop')
    plt.plot(timestamps, node_ids, 'ro', markersize=4, label='Check Point')
    plt.title('Penultimate Hop Node vs. Time (IPv6 Complete Fix)', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Relative Time (seconds)', fontsize=12)
    plt.ylabel('Active Penultimate Node / Satellite', fontsize=12)
    plt.yticks(range(len(unique_ips)), y_axis_labels)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend(loc='upper right')
    plt.tight_layout()
    plt.savefig('handover_penultimate_hop.png')
    print("图像已成功保存为 'handover_penultimate_hop.png'")
    plt.show()