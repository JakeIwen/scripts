#!/usr/bin/env python3

import socket


target_mac = bytes.fromhex("b8e8560c81f2")
magic_packet = b"\xff" * 6 + target_mac * 16

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake_socket:
    wake_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    print(wake_socket.sendto(magic_packet, ("192.168.6.255", 80)))
