#!/bin/bash
# Compatibility shim for older ignition hooks. COP ALERT now controls GPIO17
# and never reserves ext_flood; ordinary ignition lighting may always turn it off.
echo "COP ALERT uses GPIO17: ignition may turn ext_flood off"
exit 0
