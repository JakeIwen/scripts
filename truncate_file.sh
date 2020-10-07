#! /bin/bash
filename=$1
num_lines=$2
tail -n$num_lines $filename > $filename.tmp && mv $filename.tmp $filename