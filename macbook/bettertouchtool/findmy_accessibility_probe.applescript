-- Read-only probe for the Find My accessibility hierarchy.
--
-- Usage:
--   osascript macbook/bettertouchtool/findmy_accessibility_probe.applescript \
--     > tmp/findmy_accessibility.txt
--
-- Open Find My and select either Devices or Items before running this. The
-- script reports labeled controls and their available accessibility actions.
-- It does not click anything or request a sound.

on safeText(theValue)
	try
		if theValue is missing value then return ""
		return theValue as text
	on error
		return ""
	end try
end safeText

on run
	tell application "System Events"
		set matchingProcesses to application processes whose bundle identifier is "com.apple.findmy"
		if (count of matchingProcesses) is 0 then error "Open Find My before running this probe."

		set findMyProcess to item 1 of matchingProcesses
		if (count of windows of findMyProcess) is 0 then error "Find My has no open window."

		set findMyWindow to front window of findMyProcess
		set allElements to entire contents of findMyWindow
		set outputLines to {"Find My accessibility controls:"}

		repeat with currentElement in allElements
			set roleText to ""
			set nameText to ""
			set descriptionText to ""
			set valueText to ""
			set actionText to ""
			set identifierText to ""
			set positionText to ""
			set sizeText to ""

			try
				set roleText to my safeText(role of currentElement)
			end try
			try
				set nameText to my safeText(name of currentElement)
			end try
			try
				set descriptionText to my safeText(description of currentElement)
			end try
			try
				set valueText to my safeText(value of currentElement)
			end try
			try
				set actionText to my safeText(name of every action of currentElement)
			end try
			try
				set identifierText to my safeText(value of attribute "AXIdentifier" of currentElement)
			end try
			try
				set positionText to my safeText(position of currentElement)
			end try
			try
				set sizeText to my safeText(size of currentElement)
			end try

			if nameText is not "" or descriptionText is not "" or valueText is not "" or actionText is not "" then
				set end of outputLines to roleText & " | name=" & nameText & " | description=" & descriptionText & " | value=" & valueText & " | identifier=" & identifierText & " | position=" & positionText & " | size=" & sizeText & " | actions=" & actionText
			end if
		end repeat
	end tell

	set AppleScript's text item delimiters to linefeed
	set outputText to outputLines as text
	set AppleScript's text item delimiters to ""
	return outputText
end run
