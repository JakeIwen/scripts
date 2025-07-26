#!/usr/bin/env python3

import speedtest

def main():
    print("Running speed test...")

    st = speedtest.Speedtest()
    st.get_best_server()  # Finds the server with the lowest ping
    download_speed = st.download() / 1_000_000  # bits to megabits
    upload_speed = st.upload() / 1_000_000
    ping = st.results.ping  # in milliseconds

    print(f"Download Speed: {download_speed:.2f} Mbps")
    print(f"Upload Speed:   {upload_speed:.2f} Mbps")
    print(f"Latency:        {ping:.2f} ms")

if __name__ == "__main__":
    main()
