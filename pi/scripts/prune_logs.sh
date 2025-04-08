#! /bin/bash

pattern=$1
for filename in $pattern
do 
  echo "$filename"
  num_lines=1000
  tail -n$num_lines $filename > $filename.tmp && mv $filename.tmp $filename
done