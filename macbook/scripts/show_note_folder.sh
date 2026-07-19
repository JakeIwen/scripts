#! /bin/bash
# . /Users/jacobr/dev/scripts/macbook/scripts/show_note_folder.sh 'Unfinished' folder
name=$1
osascript -e "tell app \"Notes\" to show folder \"$name\""
