import socket
import struct
import threading
import time
import sys
from datetime import datetime

BIND_IP = '::'
BIND_PORT = 5005

TIMESTAMP_FORMAT = '!d'
TIMESTAMP_SIZE = struct.calcsize(TIMESTAMP_FORMAT)

packet_count = 0
bytes_received = 0
total_delay_ms = 0.0
min_delay_ms = float('inf')
max_delay_ms = 0.0
lock = threading.Lock()

def receive_data(sock):
    global packet_count, bytes_received, total_delay_ms, min_delay_ms, max_delay_ms
    while True:
        try:
            data, _ = sock.recvfrom(65535)
            recv_time = time.time()
            
            # 解析时间戳并计算单包延迟 (ms)
            if len(data) >= TIMESTAMP_SIZE:
                send_time = struct.unpack(TIMESTAMP_FORMAT, data[:TIMESTAMP_SIZE])[0]
                delay = (recv_time - send_time) * 1000.0
            else:
                delay = 0.0

            with lock:
                packet_count += 1
                bytes_received += len(data)
                total_delay_ms += delay
                if delay < min_delay_ms:
                    min_delay_ms = delay
                if delay > max_delay_ms:
                    max_delay_ms = delay

        except socket.timeout:
            pass
        except Exception:
            time.sleep(0.05)

def monitor_bandwidth():
    global packet_count, bytes_received, total_delay_ms, min_delay_ms, max_delay_ms
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now_str}] Listening for IPv6 packets on [{BIND_IP}]:{BIND_PORT}...")
    sys.stdout.flush()
    
    last_time = time.time()

    while True:
        time.sleep(1)
        now_time = time.time()
        elapsed = now_time - last_time
        
        with lock:
            current_bytes = bytes_received
            current_packets = packet_count
            curr_total_delay = total_delay_ms
            curr_min_delay = min_delay_ms
            curr_max_delay = max_delay_ms
            
            # 重置当前周期的统计变量
            packet_count = 0
            bytes_received = 0
            total_delay_ms = 0.0
            min_delay_ms = float('inf')
            max_delay_ms = 0.0
            
        last_time = now_time

        # 计算带宽 (Mbps) 与延迟统计 (ms)
        bandwidth_mbps = (current_bytes * 8) / (elapsed * 1_000_000)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if current_packets > 0:
            avg_delay = curr_total_delay / current_packets
            min_delay_str = f"{curr_min_delay:.2f}"
            max_delay_str = f"{curr_max_delay:.2f}"
            avg_delay_str = f"{avg_delay:.2f}"
        else:
            min_delay_str = max_delay_str = avg_delay_str = "0.00"

        print(
            f"[{timestamp}] [RX] Packets: {current_packets:<5} | "
            f"Bandwidth: {bandwidth_mbps:6.2f} Mbps | "
            f"Delay(ms) [Avg/Min/Max]: {avg_delay_str}/{min_delay_str}/{max_delay_str}"
        )
        sys.stdout.flush()

def main():
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    
    # 允许复用地址和端口，避免出现 Address already in use
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        
    sock.bind((BIND_IP, BIND_PORT))
    sock.settimeout(1.0)
    
    recv_thread = threading.Thread(target=receive_data, args=(sock,), daemon=True)
    recv_thread.start()
    
    try:
        monitor_bandwidth()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

if __name__ == "__main__":
    main()