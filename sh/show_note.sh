#! /bin/bash
# . /Users/jacobr/dev/scripts/sh/show_note.sh 'main todo'
name=$1
osascript -e "tell app \"Notes\" to show note \"$name\""