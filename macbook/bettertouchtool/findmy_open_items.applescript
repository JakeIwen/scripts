-- Quickly open Find My and expose its Items tab.
--
-- This deliberately does not select an item or interact with the map/detail
-- popover. It is intended for a reliable BetterTouchTool menu button.

on run
	do shell script "/usr/bin/open -b com.apple.findmy"

	tell application "System Events"
		set findMyProcess to missing value

		repeat 50 times
			set matchingProcesses to application processes whose bundle identifier is "com.apple.findmy"
			if (count of matchingProcesses) is greater than 0 then
				set findMyProcess to item 1 of matchingProcesses
				exit repeat
			end if
			delay 0.05
		end repeat

		if findMyProcess is missing value then error "Find My did not open."
		set frontmost of findMyProcess to true

		repeat 50 times
			if (count of windows of findMyProcess) is greater than 0 then exit repeat
			delay 0.05
		end repeat

		if (count of windows of findMyProcess) is 0 then error "Find My has no open window."

		set frontmost of findMyProcess to true

		-- Find My's documented shortcut for the Items list. This avoids the
		-- expensive traversal of the app's map accessibility hierarchy.
		keystroke "3" using command down
	end tell

	return "Opened Find My Items."
end run
