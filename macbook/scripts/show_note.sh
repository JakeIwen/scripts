#! /bin/bash
# . /Users/jacobr/dev/scripts/macbook/scripts/show_note.sh 'main todo'
name=$1
osascript -e "tell app \"Notes\" to show note \"$name\""
