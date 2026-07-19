#!/usr/bin/python3

from urllib.request import urlopen
import json

ip = urlopen('http://httpbin.org/ip').read()
ip = ip.decode('utf-8')
ip = json.loads(ip)

print(ip['origin'])
