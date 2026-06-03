#!/bin/bash

#
#Max-min visibility 
cd ~/NetSatBench
./nsb.py reset

# 
python3 utils/misc/apply_max_min_visibility.py \
--input-dir examples/StarPerf/Iridium/epochs \
--output-dir examples/StarPerf/Iridium/epochs-visibility-filtered

# 
python3 utils/oracle-routing.py \
--out-epoch-dir examples/StarPerf/Iridium/epochs-or-visibility \
--epoch-dir examples/StarPerf/Iridium/epochs-visibility-filtered \
--ip-version 6 \
--routing-metrics hops \
--node-type-to-route satellite,gateway --node-type-to-install satellite \
--report --redundancy --drain-before-break-offset 2

# 
python3 utils/misc/add-expected-duration.py \
--epochs-dir examples/StarPerf/Iridium/epochs-or-visibility \
--output-dir examples/StarPerf/Iridium/epochs-or-visibility-ex
#
./nsb.py run
mkdir -p ~/NetSatBench/test_final7 && cd ~/NetSatBench/test_final7
../nsb.py cp usr1:/app/ping_grd2.raw ./
mv ../ping_grd2.raw ./
ls -lh ping_grd2.raw

OUTPUT_CSV="ping_usr1_to_grd2_1s_final_fixed.csv"
echo "elapsed_seconds,icmp_seq,ttl,rtt_ms" > "$OUTPUT_CSV"

awk '
/bytes from/ {
    if (match($0, /icmp_seq=[0-9]+/)) {
        split(substr($0, RSTART, RLENGTH), s, "=");
        icmp_seq = s[2];
    } else { next; }

    if (match($0, /ttl=[0-9]+/)) {
        split(substr($0, RSTART, RLENGTH), t, "=");
        ttl = t[2];
    } else { ttl = "60"; }
    if (match($0, /time=[0-9.]+/)) {
        split(substr($0, RSTART, RLENGTH), r, "=");
        rtt = r[2];
    } else { next; }

    if (base_seq == 0) {
        base_seq = icmp_seq;
    }
    elapsed = icmp_seq - base_seq;

    print elapsed "," icmp_seq "," ttl "," rtt >> "'"$OUTPUT_CSV"'"
}
' ping_grd2.raw

echo "------------------------------------------------"
wc -l "$OUTPUT_CSV"
echo "------------Start draw chart--------------"
python3 -c "
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

df = pd.read_csv('ping_usr1_to_grd2_1s_final_fixed.csv')

df['time_min'] = df['elapsed_seconds'] / 60.0
df = df[df['time_min'] <= 20.0]


fig, ax = plt.subplots(figsize=(15, 5))


line, = ax.plot(df['time_min'], df['rtt_ms'], color='#1f77b4', linewidth=1.5, label='Measured RTT')
ax.set_xlabel('Time (min)', fontsize=12, fontweight='bold')
ax.set_ylabel('RTT (ms)', fontsize=12, fontweight='bold')
ax.set_xlim(0, 20)
ax.set_xticks(range(0, 21, 2))
ax.grid(True, linestyle='--', alpha=0.5)

topo_patch = mpatches.Patch(color='none', label='Link: user1 -> grd2')
ax.legend(handles=[line, topo_patch], loc='upper right', frameon=True, fontsize=10, facecolor='#ffffff', edgecolor='#d3d3d3')
policy_text = (
    '• SRv6-Policy\n'
    '• Minimum orbit hops\n'
)
ax.text(0.02, 0.95, policy_text, 
        transform=ax.transAxes, 
        fontsize=10, 
        verticalalignment='top', 
        horizontalalignment='left',
        bbox=dict(boxstyle='round,pad=0.5', facecolor='#fafafa', alpha=0.9, edgecolor='#e0e0e0'))

plt.tight_layout()
plt.savefig('iridium_srv6_rtt_20min_3to1.png', dpi=300)
print('📈 generate: iridium_srv6_rtt_20min_3to1.png')
"
