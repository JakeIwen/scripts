#!/bin/sh
# Stand-in for sleep(1): the manager's wait loops count elapsed time from
# their own counters, not the clock, so a brief real pause keeps the loop
# logic identical while a test no longer waits out 2-15 second sleeps.
exec /bin/sleep 0.01
