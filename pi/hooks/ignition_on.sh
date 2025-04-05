#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
mconf="$HOME/mconf"
nodisk() { rm $mconf/*; touch $mconf/nodisk; $isw; }

echo "ignition ON hook invoked"

cp -R $mconf "${mconf}_last"
nodisk
echo "IGNITION ON DONE"