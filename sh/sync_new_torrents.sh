#! /bin/bash
locpath=/Users/jacobr/Desktop/vidtmp/new
pi_user='pi@vanpi.local'
rempath="$pi_user:/mnt/movingparts/torrent/New"

selrsync(){
# selective rsync to sync only certain filetypes;
# based on: https://stackoverflow.com/a/11111793/588867
# Example: selrsync 'tsv,csv' ./source ./target --dry-run
types="$1"; shift; #accepts comma separated list of types. Must be the first argument.
includes=$(echo $types| awk  -F',' \
    'BEGIN{OFS=" ";}
    {
    for (i = 1; i <= NF; i++ ) { if (length($i) > 0) $i="--include=*."$i; } print
    }')
restargs="$@"

echo Command: rsync -avzP --prune-empty-dirs --include="*/" $includes --exclude="*" "$restargs"
eval rsync -avzP --prune-empty-dirs --include="*/" "$includes" --exclude="*" $restargs
}

date
# rsync -ur "$rempath/**/*.mkv" "$locpath"
selrsync 'mkv' "$rempath" "$locpath"
