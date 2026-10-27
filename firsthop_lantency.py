import matplotlib.pyplot as plt
from collections import defaultdict
import re

def plot_path_latency_bar(file_path):
    # 用于记录每种第一跳IP（即不同路径）对应的最终总延迟
    path_latencies = defaultdict(list)
    first_hop_ips_ordered = []

    # 读取整个文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
        
    # 以 '=== Test' 为界切分出每次的测试块 (Block)
    test_blocks = re.split(r'===\s*Test\s*at', file_content)
    
    # 编译正则表达式
    hop_pattern = re.compile(r'^\s*\d+\s+(\S+)\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms', re.MULTILINE)
    
    for block in test_blocks:
        if not block.strip():
            continue
            
        # 【连坐过滤】：如果该次测试存在 '*'，整块数据丢弃
        if '*' in block:
            continue
            
        # 提取该测试块中所有符合标准格式的跳数行
        matches = hop_pattern.findall(block)
        
        if matches:
            # 1. 提取第一行的 IP 作为坐标轴的分类标签
            first_hop_ip = matches[0][0]
            
            # 2. 提取最后一行的 3 个延迟数据用于计算平均值
            last_hop = matches[-1]
            latencies = [float(last_hop[1]), float(last_hop[2]), float(last_hop[3])]
            
            # 记录该路径分类并归入数据
            if first_hop_ip not in first_hop_ips_ordered:
                first_hop_ips_ordered.append(first_hop_ip)
            path_latencies[first_hop_ip].extend(latencies)

    if not path_latencies:
        print("所有包含超时丢包的测试块均已被过滤。没有找到有效的健康数据！")
        return

    # 按第一跳IP出现的顺序，计算其对应的最终节点平均延迟
    avg_latencies = [sum(path_latencies[ip]) / len(path_latencies[ip]) for ip in first_hop_ips_ordered]

    # 绘制柱状图
    plt.figure(figsize=(10, 6))
    bars = plt.bar(first_hop_ips_ordered, avg_latencies, color='#2ca02c', alpha=0.8, width=0.5)

    # 设置图表标题和坐标轴标签
    plt.title('Traceroute: First Hop IP vs Final Destination Latency', fontsize=14)
    plt.xlabel('First Hop IP Address ', fontsize=12)
    plt.ylabel('Average Final Latency / ms)', fontsize=12)
    
    # 将 X 轴的文字稍微倾斜，防止重叠
    plt.xticks(rotation=15)
    plt.grid(axis='y', linestyle='--', alpha=0.6)

    # 添加具体数值标签
    for bar in bars:
        yval = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, yval + 0.2, f'{yval:.2f}', ha='center', va='bottom', fontsize=10)

    # 自动调整布局并保存
    plt.tight_layout()
    plt.savefig('traceroute_path_latency_bar.png', dpi=300)
    print("图表已成功生成并保存为 traceroute_path_latency_bar.png")
    plt.show()

# 调用函数
plot_path_latency_bar('traceroute_OneWebSmall_200min_4.txt')