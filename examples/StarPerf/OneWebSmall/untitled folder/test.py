import json
import os
import re
import networkx as nx

def get_sorted_epoch_files(directory="."):
    """scan and sort epoch by number"""
    epoch_files = []
    pattern = re.compile(r"^NetSatBench-epoch(\d+)\.json$")
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            epoch_num = int(match.group(1))
            epoch_files.append((epoch_num, filename))
    epoch_files.sort(key=lambda x: x[0])
    return epoch_files

def parse_delay(delay_str):
    """delay to float '6.0ms' -> 6.0"""
    if not delay_str:
        return 0.0
    num_str = re.sub(r'[^\d.]', '', delay_str)
    return float(num_str) if num_str else 0.0

def analyze_routing_changes(directory="."):
    G = nx.Graph()
    link_epoch_history = {}
    
    source_node = 'usr1'
    target_node = 'grd7'
    previous_path = None
    

    # : {hops (int): {'total_delay': float, 'path': list, 'details': list, 'found_in_epoch': int}}
    min_delay_by_hops = {}
    
    epoch_files = get_sorted_epoch_files(directory)
    
    if not epoch_files:
        print(f"❌ do not found epoch file in '{os.path.abspath(directory)}' ")
        return
        
    print(f"✅ found {len(epoch_files)}  epoch file")
    print(f"from Epoch: {epoch_files[0][0]},end Epoch: {epoch_files[-1][0]}")
    print(f"route change rom {source_node} to {target_node} baced on delay\n")

    for epoch, filename in epoch_files:
        filepath = os.path.join(directory, filename)
            
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                print(f"⚠️ analysis {filename} fail")
                continue

        # 1. delete
        for link in data.get('links-del', []):
            u, v = link['endpoint1'], link['endpoint2']
            if G.has_edge(u, v):
                G.remove_edge(u, v)
            edge_set = frozenset([u, v])
            if edge_set in link_epoch_history:
                del link_epoch_history[edge_set]

        # 2. add
        for link in data.get('links-add', []):
            u, v = link['endpoint1'], link['endpoint2']
            delay = parse_delay(link.get('delay', '0ms'))
            G.add_edge(u, v, weight=delay)
            link_epoch_history[frozenset([u, v])] = epoch

        # 3. update
        for link in data.get('links-update', []):
            u, v = link['endpoint1'], link['endpoint2']
            delay = parse_delay(link.get('delay', '0ms'))
            G.add_edge(u, v, weight=delay)
            
            edge_set = frozenset([u, v])
            if edge_set not in link_epoch_history:
                link_epoch_history[edge_set] = epoch

        # 4. compute route
        current_path = None
        total_delay = 0.0
        
        if G.has_node(source_node) and G.has_node(target_node):
            try:
                current_path = nx.dijkstra_path(G, source=source_node, target=target_node, weight='weight')
                total_delay = nx.dijkstra_path_length(G, source=source_node, target=target_node, weight='weight')
                
                hops = len(current_path) - 1
                
                # shortest delay
                if hops not in min_delay_by_hops or total_delay < min_delay_by_hops[hops]['total_delay']:
                    path_details = []
                    for i in range(hops):
                        u = current_path[i]
                        v = current_path[i+1]
                        edge_set = frozenset([u, v])
                        added_epoch = link_epoch_history.get(edge_set, "未知")
                        link_delay = G.get_edge_data(u, v).get('weight', 0.0)
                        
                        path_details.append({
                            'u': u,
                            'v': v,
                            'delay': link_delay,
                            'added_epoch': added_epoch
                        })
                        
                    min_delay_by_hops[hops] = {
                        'total_delay': total_delay,
                        'path': current_path,
                        'details': path_details,
                        'found_in_epoch': epoch
                    }
                    
            except nx.NetworkXNoPath:
                current_path = None

        # 5. compare and print
        if current_path != previous_path:
            print("="*65)
            print(f"🔄 route modify Epoch: {epoch} (file: {filename})")
            print(f"🕒 time: {data.get('time', 'Unknown')}")
            
            if current_path is None:
                print("❌ disconnect")
            else:
                hops = len(current_path) - 1
                print(f"📏 routte hops: {hops} ")
                print(f"⏱️  delay: {total_delay:.2f} ms")
                print(f"📍 route: {' -> '.join(current_path)}")
                print("-" * 65)
                print("state track")
                
                for i in range(hops):
                    u = current_path[i]
                    v = current_path[i+1]
                    edge_set = frozenset([u, v])
                    added_epoch = link_epoch_history.get(edge_set, " does not know")
                    is_new = " (🆕 add in Epoch)" if added_epoch == epoch else ""
                    link_delay = G.get_edge_data(u, v).get('weight', 0.0)
                    print(f"  [{u:^6}] <---> [{v:^6}] : delay {link_delay:>5.1f}ms | firstly add in  Epoch {added_epoch}{is_new}")
            
            print("="*65 + "\n")
            previous_path = current_path

    print("\n" + "#"*70)
    print("📊 analysis: best delay")
    print("#"*70)
    
    if not min_delay_by_hops:
        print("  ❌ could not found route。")
    else:
        for hops in sorted(min_delay_by_hops.keys()):
            record = min_delay_by_hops[hops]
            print(f"🟢 【 {hops:>2} hops 】best delay: {record['total_delay']:.2f} ms (in Epoch {record['found_in_epoch']})")
            print(f"   📍 route: {' -> '.join(record['path'])}")
            print("   🔗 detail:")
            
            for detail in record['details']:
                u = detail['u']
                v = detail['v']
                d = detail['delay']
                add_ep = detail['added_epoch']
                print(f"       [{u:^6}] <---> [{v:^6}] : delay {d:>5.1f}ms |  add in Epoch {add_ep}")
            print("-" * 70)

if __name__ == "__main__":
    analyze_routing_changes(".")