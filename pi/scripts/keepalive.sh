#! /bin/bash

airsonos() {
  if [[ `ps ax` != *'airupnp-arm'* ]] &> /dev/null 
  then sudo service airupnp start
  fi
}

nativcast() {
  if [[ `ps ax` != *'NativCast/server.py'* ]] &> /dev/null 
  then  python3 /home/pi/NativCast/server.py 
  fi
}

airsonos
# nativcast

  