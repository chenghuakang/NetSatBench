from datetime import datetime
import re
import matplotlib.pyplot as plt

# =====================================================================
# 1. 智能解析 traceroute_20min.txt 文件 (提取最终跳数与总延迟)
# =====================================================================
file_path = "traceroute_20min.txt"

data_blocks = []
current_block = {"timestamp": None, "max_hop": None, "delay": None}

try:
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            # 1. 匹配时间戳，划分测试区块
            time_match = re.search(r"=== Test at (.+?) ===", line)
            if time_match:
                # 遇到新时间戳，保存上一个有效区块
                if current_block["timestamp"] is not None and current_block["max_hop"] is not None:
                    data_blocks.append(current_block)
                
                time_str = time_match.group(1)
                try:
                    cleaned_time_str = re.sub(r"\b[A-Z]{3,4}\b", "", time_str).strip()
                    cleaned_time_str = " ".join(cleaned_time_str.split())
                    dt = datetime.strptime(cleaned_time_str, "%a %b %d %H:%M:%S %Y")
                    current_block = {"timestamp": dt, "max_hop": None, "delay": None}
                except Exception:
                    current_block = {"timestamp": None, "max_hop": None, "delay": None}
                continue

            # 2. 提取每跳的数据
            hop_match = re.match(r"^\s*(\d+)\s+(.+)", line)
            if current_block["timestamp"] is not None and hop_match:
                hop_num = int(hop_match.group(1))
                rest_of_line = hop_match.group(2)
                
                # 提取该行所有的延迟数值 (例如 98.162 ms)
                delays = re.findall(r"([\d\.]+)\s*ms", rest_of_line)
                
                if delays:
                    # 取该跳的平均延迟
                    avg_delay = sum(float(d) for d in delays) / len(delays)
                    # 不断覆盖更新，这样循环结束时，保留的就是“最后一跳（到达目的地）”的跳数和延迟
                    current_block["max_hop"] = hop_num
                    current_block["delay"] = avg_delay

    # 收尾最后一个区块
    if current_block["timestamp"] is not None and current_block["max_hop"] is not None:
        data_blocks.append(current_block)
except FileNotFoundError:
    print(f"错误：在当前目录下未找到日志文件 '{file_path}'！")
    exit()

# =====================================================================
# 2. 换算相对时间与提取数据维度
# =====================================================================
timestamps = []
hop_counts = []
delays = []
start_time = None

if data_blocks:
    start_time = data_blocks[0]["timestamp"]

for block in data_blocks:
    relative_seconds = (block["timestamp"] - start_time).total_seconds()
    timestamps.append(relative_seconds)
    hop_counts.append(block["max_hop"])
    delays.append(block["delay"])

print(f"成功解析了 {len(timestamps)} 组完整的 端到端跳数/延迟 数据。")

# =====================================================================
# 3. 绘制双 Y 轴图表 (Twin Axes)
# =====================================================================
if not timestamps:
    print("错误：未解析到有效数据！")
else:
    # 创建主画布和主坐标轴 (左轴: 跳数)
    fig, ax1 = plt.subplots(figsize=(14, 7), dpi=100)
    
    color1 = '#1f77b4' # 科技蓝
    ax1.set_xlabel('Relative Time (seconds)', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Total Hop Count (Step Line)', color=color1, fontsize=12, fontweight='bold')
    
    # 画跳数阶梯图 (离散变量)
    line1 = ax1.step(timestamps, hop_counts, where='post', color=color1, linewidth=2.5, marker='o', label='Hop Count')
    ax1.tick_params(axis='y', labelcolor=color1)
    
    # 强制跳数的 Y 轴只显示整数
    ax1.set_yticks(range(min(hop_counts) - 1, max(hop_counts) + 2))
    
    # 创建共用 X 轴的副坐标轴 (右轴: 延迟)
    ax2 = ax1.twinx()  
    
    color2 = '#d62728' # 警示红
    ax2.set_ylabel('End-to-End Delay (ms)', color=color2, fontsize=12, fontweight='bold')
    
    # 画延迟折线图 (连续变量)
    line2 = ax2.plot(timestamps, delays, color=color2, linewidth=2, marker='s', linestyle='-', alpha=0.8, label='Total Delay')
    ax2.tick_params(axis='y', labelcolor=color2)
    
    # 标题和网格
    plt.title('Impact of Satellite Handover & Routing on E2E Delay', fontsize=15, fontweight='bold', pad=15)
    ax1.grid(True, linestyle='--', alpha=0.5)
    
    # 合并两个轴的图例
    lines = line1 + line2
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False, fontsize=12)
    
    # 自动调整布局防边缘裁剪
    fig.tight_layout()
    plt.subplots_adjust(bottom=0.15) # 给底部图例留点空间
    
    plt.savefig('delay_vs_hops_analysis.png')
    print("图像已成功保存为 'delay_vs_hops_analysis.png'")
    plt.show()