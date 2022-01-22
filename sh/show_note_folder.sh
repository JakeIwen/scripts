#! /bin/bash
# . /Users/jacobr/dev/scripts/sh/show_note.sh 'Unfinished' folder
name=$1
osascript -e "tell app \"Notes\" to show folder \"$name\""