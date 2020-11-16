# From the code for the Box 1 kit for the Raspberry Pi by MonkMakes.com

from gpiozero import Button, RGBLED
from colorzero import Color
from sonos_tasks import adjust_volume, start_noise, set_partymode_vol
import time, requests, 
import soco

Button().when_pressed = adjust_volume("vonFront", "up", 5)
Button().when_pressed = adjust_volume("vonFront", "up", 5)
Button().when_pressed = adjust_volume("vonMid", "up", 5)
Button().when_pressed = adjust_volume("vonMid", "down", 5)
Button().when_pressed = adjust_volume("vonRear", "down", 5)
Button().when_pressed = adjust_volume("vonRear", "down", 5)
    
Button().when_pressed = start_noise()




while True: 
   try:
       cheerlights = requests.get(cheerlights_url)
       color = cheerlights.content             # the color as text
       if color != old_color:
           led.color = Color(color)            # the color as an object
           old_color = color           
   except Exception as e:
       print(e)
   time.sleep(0.2)           # don't flood the web service