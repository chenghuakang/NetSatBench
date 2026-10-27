import matplotlib.pyplot as plt
from collections import defaultdict
import re
import numpy as np
import scipy.stats as st

def plot_total_hops_vs_final_latency(file_path):
    hop_count_latencies = defaultdict(list)

    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
        
    test_blocks = re.split(r'===\s*Test\s*at', file_content)
    hop_pattern = re.compile(r'^\s*(\d+)\s+(\S+)\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms\s+([0-9\.]+)\s+ms', re.MULTILINE)
    
    for block in test_blocks:
        if not block.strip() or '*' in block:
            continue
        matches = hop_pattern.findall(block)
        if matches:
            total_hops = int(matches[-1][0])
            if total_hops > 2:
                latencies = [float(matches[-1][2]), float(matches[-1][3]), float(matches[-1][4])]
                hop_count_latencies[total_hops].extend(latencies)

    if not hop_count_latencies:
        print("do not found data！")
        return

    hops = sorted(hop_count_latencies.keys())
    data_to_plot = [hop_count_latencies[h] for h in hops]

    plt.figure(figsize=(18, 10))
    
    bp = plt.boxplot(data_to_plot, positions=hops, widths=0.4, patch_artist=True)
    
    for box in bp['boxes']:
        box.set(facecolor='lightblue', linewidth=1.5)
    for median_line in bp['medians']:
        median_line.set(color='red', linewidth=2)

    theoretical_hops = [21,22,23, 24, 25, 26]
    theoretical_latencies = [140.01,146.01,152.01, 152.01, 164.01, 172.01]
    plt.plot(theoretical_hops, theoretical_latencies, color='orange', linestyle='--', 
             marker='s', label='Theoretical Min Latency', linewidth=2, markersize=8)
    
    for x, y in zip(theoretical_hops, theoretical_latencies):
        plt.text(x, y + 2, f'{y:.2f}', ha='center', fontsize=8, color='orange')

    for h in hops:
        data = np.array(hop_count_latencies[h])
        mean_val = np.mean(data)
        med_val = np.median(data)
        
        # 95% confident interval
        if len(data) > 1:
            ci = st.t.interval(0.95, len(data)-1, loc=mean_val, scale=st.sem(data))
        else:
            ci = (mean_val, mean_val)
            
        ci_l, ci_u = ci[0], ci[1]

        # CI
        plt.errorbar(h, mean_val, yerr=[[mean_val-ci_l], [ci_u-mean_val]], 
                     fmt='none', ecolor='green', elinewidth=2, capsize=5)

        offset = 0.3
        if ci_u - ci_l < 1.5:
            plt.text(h + offset, ci_l - 1.0, f'L:{ci_l:.1f}', fontsize=7, color='green', va='center')
            plt.text(h + offset, ci_u + 1.0, f'U:{ci_u:.1f}', fontsize=7, color='green', va='center')
        else:
            plt.text(h + offset, ci_l, f'L:{ci_l:.1f}', fontsize=7, color='green', va='center')
            plt.text(h + offset, ci_u, f'U:{ci_u:.1f}', fontsize=7, color='green', va='center')
        
        plt.text(h - offset, med_val, f'Med:{med_val:.1f}', fontsize=7, color='red', ha='right', va='center', fontweight='bold')

    plt.xticks(sorted(list(set(hops) | set(theoretical_hops))))
    plt.grid(True, linestyle='--', alpha=0.5, axis='y')
    plt.legend(loc='upper left')
    plt.tight_layout()
    plt.show()

plot_total_hops_vs_final_latency('traceroute_OneWebSmall_min_lifetime_120s2.txt')