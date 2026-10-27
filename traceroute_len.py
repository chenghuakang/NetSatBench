import re
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def plot_path_length(file_path):
    times = []
    path_lengths = []
    
    current_time = None
    current_hops = 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            time_match = re.search(r'===\s*Test at (.*?)\s*===', line)
            if time_match:
                if current_time is not None:
                    times.append(current_time)
                    path_lengths.append(current_hops)
                
                time_str = time_match.group(1)
                time_str_clean = time_str.replace(' UTC ', ' ')
                current_time = datetime.strptime(time_str_clean, '%a %b %d %H:%M:%S %Y')
                current_hops = 0  
                continue
            
            hop_match = re.match(r'^\s*(\d+)\s+', line)
            if hop_match:
                current_hops = max(current_hops, int(hop_match.group(1)))
                
        if current_time is not None:
            times.append(current_time)
            path_lengths.append(current_hops)

    if not times:
        print("未在文件中找到有效数据！")
        return

    plt.figure(figsize=(12, 6))
    
    plt.plot(times, path_lengths, marker='o', linestyle='-', color='b', drawstyle='steps-post')
    
    plt.xlabel('Time (UTC)')
    plt.ylabel('Path Length (Hops)')
    plt.title('Network Path Length over Time')

    max_hop = max(path_lengths) if path_lengths else 0
    plt.yticks(range(0, max_hop + 3))

    start_time = times[0]
    end_time = start_time + timedelta(minutes=20)
    plt.xlim(start_time, end_time)

    ax = plt.gca()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.tight_layout()
    
    plt.savefig('path_length_20min_plot.png', dpi=300)
    plt.show()

if __name__ == '__main__':
    plot_path_length('.txt')