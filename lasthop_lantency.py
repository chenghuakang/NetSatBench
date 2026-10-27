import matplotlib.pyplot as plt
from collections import defaultdict
import re

def plot_last_hop_src_latency(file_path):
    last_hop_src_latencies = defaultdict(list)
    ips_ordered = []

    # 1. 读取整个文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
        
    # 以 '=== Test' 为界切分出每次的测试块 (Block)
    test_blocks = re.split(r'===\s*Test\s*at', file_content)
    
    # 2. 编译正则表达式，严格匹配包含3个ms延迟数值的行
    hop_pattern = re.compile(r'^\s*\d+\s+(\S+)\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms', re.MULTILINE)
    
    for block in test_blocks:
        if not block.strip():
            continue
            
        # 【最严格连坐过滤】：如果该次测试任何地方存在 '*'，整块数据丢弃
        if '*' in block:
            continue
            
        # 3. 提取该测试块中所有符合标准格式的跳数行
        matches = hop_pattern.findall(block)
        
        # 确保至少有两跳数据，才能安全地取到"倒数第二跳"
        if len(matches) >= 2:
            # A. 提取倒数第二行的 IP 作为坐标轴的分类标签
            second_last_hop_ip = matches[-2][0]
            
            # B. 提取最后一行的 3 个延迟数据用于计算【最终目标】的延迟
            last_hop = matches[-1]
            latencies = [float(last_hop[1]), float(last_hop[2]), float(last_hop[3])]
            
            # 记录该路径分类并归入数据池
            if second_last_hop_ip not in ips_ordered:
                ips_ordered.append(second_last_hop_ip)
            last_hop_src_latencies[second_last_hop_ip].extend(latencies)

    # 4. 数据绘图部分
    if not last_hop_src_latencies:
        print("所有包含超时丢包的测试块均已被过滤。没有找到有效的健康数据！")
        return

    # 按出现顺序，计算不同倒数第二跳对应的【最终节点平均延迟】
    avg_latencies = [sum(last_hop_src_latencies[ip]) / len(last_hop_src_latencies[ip]) for ip in ips_ordered]

    # 绘制柱状图，保留你的红色主题
    plt.figure(figsize=(8, 5))
    bars = plt.bar(ips_ordered, avg_latencies, color='#d62728', alpha=0.8, width=0.3)

    plt.title('Traceroute: Last Hop Source IP vs Final Destination Latency', fontsize=14)
    plt.xlabel('Last Hop Source IP ', fontsize=12)
    plt.ylabel('Average Final Latency / ms)', fontsize=12)
    
    # 动态判断：如果有多个IP就倾斜显示防止重叠
    plt.xticks(rotation=15 if len(ips_ordered) > 2 else 0)
    plt.grid(axis='y', linestyle='--', alpha=0.6)

    # 在每个柱子上方添加精确数值
    for bar in bars:
        yval = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, yval + 0.1, f'{yval:.2f}', ha='center', va='bottom', fontsize=10)

    plt.tight_layout()
    plt.savefig('traceroute_last_hop_src_bar.png', dpi=300)
    print("图表已成功生成并保存为 traceroute_last_hop_src_bar.png")
    plt.show()

# 调用函数
plot_last_hop_src_latency('traceroute_OneWebSmall_200min_4.txt')