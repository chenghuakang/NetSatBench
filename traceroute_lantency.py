import matplotlib.pyplot as plt
from collections import defaultdict
import re
import numpy as np

def plot_total_hops_vs_final_latency(file_path):
    hop_count_latencies = defaultdict(list)

    # 1. 读取整个文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
        
    test_blocks = re.split(r'===\s*Test\s*at', file_content)
    hop_pattern = re.compile(r'^\s*(\d+)\s+(\S+)\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms', re.MULTILINE)
    
    for block in test_blocks:
        if not block.strip():
            continue
            
        # 过滤存在 '*' 的超时测试块
        if '*' in block:
            continue
            
        matches = hop_pattern.findall(block)
        
        if matches:
            last_hop = matches[-1]
            total_hops = int(last_hop[0])
            latencies = [float(last_hop[2]), float(last_hop[3]), float(last_hop[4])]
            hop_count_latencies[total_hops].extend(latencies)

    if not hop_count_latencies:
        print("所有包含超时丢包的测试块均已被过滤。没有找到有效的健康数据！")
        return

    hops = sorted(hop_count_latencies.keys())
    data_to_plot = [hop_count_latencies[h] for h in hops]

    # 4. 绘制箱线图
    plt.figure(figsize=(14, 8)) # 稍微再加宽一点，给左侧的平均值标注留出空间
    
    # 开启 showmeans=True，并自定义平均值标记的样式（绿色菱形）
    mean_style = {"marker": "D", "markerfacecolor": "green", "markeredgecolor": "green", "markersize": 6}
    bp = plt.boxplot(data_to_plot, positions=hops, widths=0.4, patch_artist=True, 
                     showmeans=True, meanprops=mean_style)
    
    # 美化箱体和中位数线条
    for box in bp['boxes']:
        box.set(facecolor='lightblue', linewidth=1.5) # 已经去掉了 # 号
    for median_line in bp['medians']:
        median_line.set(color='red', linewidth=2)

    # 5. 计算 6 个核心统计量，并在图表上添加文本标注
    print("--- 各跳数延迟统计信息 ---")
    for i, h in enumerate(hops):
        group_data = hop_count_latencies[h]
        
        minimum = np.min(group_data)
        q1 = np.percentile(group_data, 25)
        median = np.median(group_data)
        mean_val = np.mean(group_data) # 新增计算平均值
        q3 = np.percentile(group_data, 75)
        maximum = np.max(group_data)
        
        print(f"总跳数 {h:2d} -> Min: {minimum:6.2f}, Q1: {q1:6.2f}, Median: {median:6.2f}, Mean: {mean_val:6.2f}, Q3: {q3:6.2f}, Max: {maximum:6.2f}")
        
        # 标注：极值和四分位数放在箱体右侧
        text_x_right = h + 0.25
        plt.text(text_x_right, minimum, f'Min: {minimum:.2f}', va='center', ha='left', fontsize=9, color='black')
        plt.text(text_x_right, q1, f'Q1: {q1:.2f}', va='center', ha='left', fontsize=9, color='#1f77b4')
        plt.text(text_x_right, median, f'Med: {median:.2f}', va='center', ha='left', fontsize=9, color='red', fontweight='bold')
        plt.text(text_x_right, q3, f'Q3: {q3:.2f}', va='center', ha='left', fontsize=9, color='#1f77b4')
        plt.text(text_x_right, maximum, f'Max: {maximum:.2f}', va='center', ha='left', fontsize=9, color='black')

        # 标注：平均值放在箱体左侧，使用绿色
        text_x_left = h - 0.25
        plt.text(text_x_left, mean_val, f'Mean: {mean_val:.2f}', va='center', ha='right', fontsize=9, color='green', fontweight='bold')

    plt.title('Traceroute: Total Hops vs Final Destination Latency (Boxplot with Mean)', fontsize=14)
    plt.xlabel('Total Hops to Destination', fontsize=12)
    plt.ylabel('Final Latency / ms', fontsize=12)
    
    plt.xticks(hops)
    plt.grid(True, linestyle='--', alpha=0.6, axis='y')
    
    plt.tight_layout()
    plt.savefig('traceroute_OneWebSmall_hopsboxplot_mean.png', dpi=300)
    print("-------------------------")
    print("traceroute_OneWebSmall_hops_boxplot_mean.png")
    plt.show()

plot_total_hops_vs_final_latency('traceroute_OneWebSmall_hops.txt')