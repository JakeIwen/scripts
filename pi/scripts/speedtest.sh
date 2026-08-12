#!/bin/bash

ping_output=$(ping -c 1 -W 2 8.8.8.8 2>&1)
if [ $? -ne 0 ]; then
  echo "Ping to 8.8.8.8 failed. Aborting speedtest."
  exit 1
fi

route_ping=$(printf '%s\n' "$ping_output" | awk -F' = ' '
  /^(rtt|round-trip) / {
    split($2, values, "/")
    if (values[2] ~ /^[0-9]+([.][0-9]+)?$/) print values[2]
    exit
  }
')

if ! output=$(speedtest-cli --simple); then
  echo "speedtest-cli failed."
  exit 1
fi
download=$(echo "$output" | grep "Download" | awk '{print $2}')
upload=$(echo "$output" | grep "Upload" | awk '{print $2}')
ping=$(echo "$output" | grep "Ping" | awk '{print $2}')

# speedtest-cli 2.1.3 substitutes 3600 seconds for each failed latency probe.
# When every probe fails it reports 1800000 ms even though the throughput test
# can still succeed. Reject any impossible minute-plus result and use the
# already successful route reachability ping instead.
if ! awk -v value="$ping" 'BEGIN {
  exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value < 60000)
}'; then
  if ! awk -v value="$route_ping" 'BEGIN {
    exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value < 60000)
  }'; then
    echo "speedtest-cli returned an invalid latency and route ping was unavailable."
    exit 1
  fi
  ping=$route_ping
fi

echo "Download Speed: $download Mbps"
echo "Upload Speed:   $upload Mbps"
echo "Latency:        $ping ms"
