#!/bin/bash

if ! ping -c 1 -W 2 8.8.8.8 > /dev/null; then
  echo "Ping to 8.8.8.8 failed. Aborting speedtest."
  exit 1
fi

output=$(speedtest-cli --simple)
download=$(echo "$output" | grep "Download" | awk '{print $2}')
upload=$(echo "$output" | grep "Upload" | awk '{print $2}')
ping=$(echo "$output" | grep "Ping" | awk '{print $2}')

echo "Download Speed: $download Mbps"
echo "Upload Speed:   $upload Mbps"
echo "Latency:        $ping ms"
