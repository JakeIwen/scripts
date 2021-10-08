#!/bin/bash
cmd=$1
mac=$2
bluetoothctl << EOF
$cmd $mac
EOF
