#! /bin/bash
task=$1

case "$#" in
  "1")
    args="";;
  "2")
    args="'$2'";; 
  "3")
    args="'$2', '$3'";;
  "4")
    args="'$2', '$3', '$4'";;
esac

py_cmd="from sonos_tasks import $task; $task($args)"

echo "setting system volume to 90%"
amixer cset numid=1 90%

echo  "running python3: $py_cmd"
python3 -c "$py_cmd"

