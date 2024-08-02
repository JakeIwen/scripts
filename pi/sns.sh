#! /bin/bash
task=$1

if [[ "$#" = "2" ]]
then args="'$2'"
elif [[ "$#" = "3" ]]
then args="'$2', '$3'"
elif [[ "$#" = "4" ]]
then args="'$2', '$3', '$4'"
fi

py_cmd="from sonos_tasks import $task; $task($args)"

echo "setting system volume to 90%"
amixer cset numid=1 90%

echo  "running python3: $py_cmd"
python3 -c "$py_cmd"

