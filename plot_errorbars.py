import pandas as pd
import matplotlib.pyplot as plt

datasets = [
    {"file": "test_final4/ping_usr1_to_grd2_1s_pure_correct.csv", "label": "Minimum orbit hops", "color": "#1f77b4"},
    {"file": "test_final5/ping_usr1_to_grd2_1s_pure_correct.csv", "label": "Min access delay", "color": "#ff7f0e"},
    {"file": "test_final6/ping_usr1_to_grd2_1s_pure_correct.csv", "label": "Minimum lifetime", "color": "#2ca02c"},
    {"file": "test_final7/ping_usr1_to_grd2_1s_pure_correct.csv", "label": "Max-min visibility", "color": "#d62728"}
]

fig, ax = plt.subplots(figsize=(8, 6))

x_coords = []
active_labels = []
current_x = 1

print(f"{'Link Label':<22} {'Min':<8} {'25%':<8} {'Median':<8} {'Mean':<8} {'75%':<8} {'Max':<8}")
print("-" * 75)

for data in datasets:
    try:
        df = pd.read_csv(data["file"])
        df["time_min"] = df["elapsed_seconds"] / 60.0
        df = df[df["time_min"] <= 20.0]
        rtt = df["rtt_ms"].dropna()
        
        v_min = rtt.min()
        v_25 = rtt.quantile(0.25)
        v_median = rtt.median()
        v_mean = rtt.mean()
        v_75 = rtt.quantile(0.75)
        v_max = rtt.max()
        
        print(f"{data['label']:<22} {v_min:<8.2f} {v_25:<8.2f} {v_median:<8.2f} {v_mean:<8.2f} {v_75:<8.2f} {v_max:<8.2f}")
        
        color = data["color"]
        
        ax.errorbar(current_x, v_median, 
                    yerr=[[v_median - v_min], [v_max - v_median]], 
                    fmt='none', ecolor=color, elinewidth=1.2, capsize=6, capthick=1.2, zorder=1)
        
        err_low = max(0, v_median - v_25)
        err_high = max(0, v_75 - v_median)
        ax.errorbar(current_x, v_median, 
                    yerr=[[err_low], [err_high]], 
                    fmt='none', ecolor=color, elinewidth=5.0, capsize=0, zorder=2)
        
        ax.plot([current_x - 0.15, current_x + 0.15], [v_median, v_median], color='#222222', linewidth=2.5, zorder=3)
        
        ax.plot(current_x, v_mean, marker='*', color='#d62728', markersize=10, markeredgecolor='black', markeredgewidth=0.5, zorder=4)
        
        x_coords.append(current_x)
        active_labels.append(data["label"])
        current_x += 1
        
    except FileNotFoundError:
        print(f"⚠️ 未找到文件: {data['file']}，已跳过。")

ax.set_ylabel('RTT (ms)', fontsize=12, fontweight='bold')
ax.set_xticks(x_coords)
ax.set_xticklabels(active_labels, fontsize=10, fontweight='bold', rotation=15) 
ax.set_xlim(0.5, len(x_coords) + 0.5)
ax.grid(True, linestyle='--', alpha=0.4, axis='y')

expl_text = (
    '★ Red Star: Mean\n'
    '• Black Line: Median\n'
    '• Thick Bar: 25% - 75%\n'
    '• Thin Line: Min - Max'
)
ax.text(0.03, 0.95, expl_text, transform=ax.transAxes, fontsize=9, verticalalignment='top', bbox=dict(boxstyle='round,pad=0.4', facecolor='#fafafa', alpha=0.9, edgecolor='#e0e0e0'))

policy_text = '• SRv6-Policy\n• user1->grd2\n'
ax.text(0.97, 0.95, policy_text, transform=ax.transAxes, fontsize=9, verticalalignment='top', horizontalalignment='right', bbox=dict(boxstyle='round,pad=0.4', facecolor='#fafafa', alpha=0.9, edgecolor='#e0e0e0'))

plt.tight_layout()
output_filename = 'iridium_srv6_rtt_summary_errorbars.png'
plt.savefig(output_filename, dpi=300)
print(f'\n📈  {output_filename}')
