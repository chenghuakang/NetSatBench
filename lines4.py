import pandas as pd
import matplotlib.pyplot as plt

datasets = [
    {
        'file': 'test_final4/ping_usr1_to_grd2_1s_pure_correct.csv', 
        'label': 'Minimum orbit hops', 
        'color': '#1f77b4'  
    },
    {
        'file': 'test_final5/ping_usr1_to_grd2_1s_pure_correct.csv',  
        'label': 'Min access delay', 
        'color': '#ff7f0e'  
    },
    {
        'file': ‘test_final6/ping_usr1_to_grd2_1s_pure_correct.csv’, 
        'label': 'Minimum lifetime', 
        'color': '#2ca02c'  
    },
    {
        'file': 'test_final7/ping_usr1_to_grd2_1s_pure_correct.csv’,  
        'label': 'Max-min visibility', 
        'color': '#d62728'  
    }
]

fig, ax = plt.subplots(figsize=(15, 5))
for data in datasets:
    try:
        df = pd.read_csv(data['file'])
        df['time_min'] = df['elapsed_seconds'] / 60.0
        df = df[df['time_min'] <= 20.0]
        ax.plot(df['time_min'], df['rtt_ms'], color=data['color'], linewidth=1.5, label=data['label'])
        
    except FileNotFoundError:
        print(f"⚠️ {data['file']} does exist")

ax.set_xlabel('Time (min)', fontsize=12, fontweight='bold')
ax.set_ylabel('RTT (ms)', fontsize=12, fontweight='bold')
ax.set_xlim(0, 20)
ax.set_xticks(range(0, 21, 2))
ax.grid(True, linestyle='--', alpha=0.5)

ax.legend(loc='upper right', frameon=True, fontsize=10, facecolor='#ffffff', edgecolor='#d3d3d3')

policy_text = (
    '• SRv6-Policy\n'
    '• Link: user1 -> grd2\n'
)
ax.text(0.02, 0.95, policy_text, 
        transform=ax.transAxes, 
        fontsize=10, 
        verticalalignment='top', 
        horizontalalignment='left',
        bbox=dict(boxstyle='round,pad=0.5', facecolor='#fafafa', alpha=0.9, edgecolor='#e0e0e0'))

plt.tight_layout()
output_filename = 'iridium_srv6_rtt_20min_3to1_multi.png'
plt.savefig(output_filename, dpi=300)
print(f'📈 saved in {output_filename}')
