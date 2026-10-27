import socket
import struct
import time
import sys
from datetime import datetime

TARGET_IP = '2001:db8:101::1b'  # grd7 接收端地址
TARGET_PORT = 5005
PACKET_SIZE = 1200              # 规避 1408 MTU 丢包
TARGET_MBPS = 50                #  50m/1200 = 4000

# 8字节双精度浮点数 (大端序网络格式)
TIMESTAMP_FORMAT = '!d'
TIMESTAMP_SIZE = struct.calcsize(TIMESTAMP_FORMAT)

def main():
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    
    # 填充部分：总大小减去8字节时间戳
    padding = b'x' * (PACKET_SIZE - TIMESTAMP_SIZE)
    
    bytes_per_sec = (TARGET_MBPS * 1_000_000) / 8
    packets_per_sec = bytes_per_sec / PACKET_SIZE
    interval = 1.0 / packets_per_sec

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now_str}] Sending to [{TARGET_IP}]:{TARGET_PORT} | Rate: {TARGET_MBPS} Mbps | Size: {PACKET_SIZE}B")
    sys.stdout.flush()

    sent_packets = 0
    sent_bytes = 0
    last_stat_time = time.time()
    next_send_time = time.time()

    while True:
        try:
            # 打包当前时间戳并与填充拼接
            ts_bytes = struct.pack(TIMESTAMP_FORMAT, time.time())
            packet = ts_bytes + padding

            sock.sendto(packet, (TARGET_IP, TARGET_PORT))
            sent_packets += 1
            sent_bytes += len(packet)
            next_send_time += interval

            sleep_duration = next_send_time - time.time()
            if sleep_duration > 0:
                time.sleep(sleep_duration)
            elif sleep_duration < -0.05:
                next_send_time = time.time()

        except BlockingIOError:
            time.sleep(0.005)
        except OSError:
            time.sleep(0.05)
            next_send_time = time.time()
        except KeyboardInterrupt:
            break

        now = time.time()
        elapsed = now - last_stat_time
        if elapsed >= 1.0:
            rate = (sent_bytes * 8) / (elapsed * 1_000_000)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{timestamp}] [TX] Total_Sent: {sent_packets} | Rate: {rate:.2f} Mbps")
            sys.stdout.flush()
            
            sent_bytes = 0
            last_stat_time = now

if __name__ == "__main__":
    main()