-- EXPERIMENTAL: this UI-automation path is slow and remains intermittent.
-- Prefer findmy_open_items.zsh for a reliable BetterTouchTool button.
--
-- Select a Find My device or item by its exact displayed title and optionally
-- invoke its contextual "Play Sound" action.
--
-- Usage:
--   osascript findmy_play_sound.applescript devices "lion_fone" --dry-run
--   osascript findmy_play_sound.applescript items "Dezi" --menu-only
--   osascript findmy_play_sound.applescript devices "lion_fone" --play
--
-- If Find My contains duplicate exact titles, provide a 1-based occurrence as
-- a fourth argument. Without it, the script fails closed on ambiguous titles.

on safeText(theValue)
	try
		if theValue is missing value then return ""
		return theValue as text
	on error
		return ""
	end try
end safeText

on matchingElements(rootElement, desiredRole, desiredLabel)
	tell application "System Events"
		set allElements to entire contents of rootElement
		set foundElements to {}

		repeat with currentElement in allElements
			set roleText to ""
			set nameText to ""
			set descriptionText to ""

			try
				set roleText to my safeText(role of currentElement)
			end try
			try
				set nameText to my safeText(name of currentElement)
			end try
			try
				set descriptionText to my safeText(description of currentElement)
			end try

			if roleText is desiredRole and (nameText is desiredLabel or descriptionText is desiredLabel) then
				set end of foundElements to contents of currentElement
			end if
		end repeat
	end tell

	return foundElements
end matchingElements

on matchingElementsByIdentifier(rootElement, desiredRole, desiredIdentifier)
	tell application "System Events"
		set allElements to entire contents of rootElement
		set foundElements to {}

		repeat with currentElement in allElements
			set roleText to ""
			set identifierText to ""

			try
				set roleText to my safeText(role of currentElement)
			end try
			try
				set identifierText to my safeText(value of attribute "AXIdentifier" of currentElement)
			end try

			if roleText is desiredRole and identifierText is desiredIdentifier then
				set end of foundElements to contents of currentElement
			end if
		end repeat
	end tell

	return foundElements
end matchingElementsByIdentifier

on matchingListTitles(rootElement, desiredLabel)
	tell application "System Events"
		set allElements to entire contents of rootElement
		set foundElements to {}

		repeat with currentElement in allElements
			set roleText to ""
			set nameText to ""
			set descriptionText to ""
			set identifierText to ""

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
				set identifierText to my safeText(value of attribute "AXIdentifier" of currentElement)
			end try

			-- Find My's sidebar rows use identifiers such as
			-- HomeCellTitleLabel. Map callout titles have no CellTitleLabel
			-- identifier, so this remains stable when the window moves.
			if roleText is "AXStaticText" and (nameText is desiredLabel or descriptionText is desiredLabel) and identifierText contains "CellTitleLabel" then
				set end of foundElements to contents of currentElement
			end if
		end repeat
	end tell

	return foundElements
end matchingListTitles

on matchingMapCardTitles(rootElement, desiredLabel)
	tell application "System Events"
		set allElements to entire contents of rootElement
		set foundElements to {}

		repeat with currentElement in allElements
			set roleText to ""
			set nameText to ""
			set descriptionText to ""
			set identifierText to ""

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
				set identifierText to my safeText(value of attribute "AXIdentifier" of currentElement)
			end try

			if roleText is "AXStaticText" and (nameText is desiredLabel or descriptionText is desiredLabel) and identifierText does not contain "CellTitleLabel" then
				set end of foundElements to contents of currentElement
			end if
		end repeat
	end tell

	return foundElements
end matchingMapCardTitles

on matchingInfoButtons(rootElement)
	tell application "System Events"
		set allElements to entire contents of rootElement
		set foundElements to {}

		repeat with currentElement in allElements
			set roleText to ""
			set nameText to ""
			set descriptionText to ""

			try
				set roleText to my safeText(role of currentElement)
			end try
			try
				set nameText to my safeText(name of currentElement)
			end try
			try
				set descriptionText to my safeText(description of currentElement)
			end try

			if roleText is "AXButton" then
				if nameText contains "Info" or nameText contains "info" or descriptionText contains "Info" or descriptionText contains "info" then
					set end of foundElements to contents of currentElement
				end if
			end if
		end repeat
	end tell

	return foundElements
end matchingInfoButtons

on performAXAction(targetElement, actionName)
	tell application "System Events"
		set availableActions to name of every action of targetElement
		if availableActions does not contain actionName then error "The selected control does not support " & actionName & "."
		perform action actionName of targetElement
	end tell
end performAXAction

on clickElementCenter(targetElement)
	tell application "System Events"
		set elementPosition to position of targetElement
		set elementSize to size of targetElement
		set clickX to (item 1 of elementPosition) + ((item 1 of elementSize) div 2)
		set clickY to (item 2 of elementPosition) + ((item 2 of elementSize) div 2)
	end tell

	set clickHelper to system attribute "FINDMY_CLICK_HELPER"
	if clickHelper is "" then error "The CoreGraphics click helper was not configured."
	do shell script quoted form of clickHelper & " " & (clickX as text) & " " & (clickY as text)
end clickElementCenter

on waitForFindMyProcess()
	tell application "System Events"
		repeat 50 times
			set matchingProcesses to application processes whose bundle identifier is "com.apple.findmy"
			if (count of matchingProcesses) is greater than 0 then return item 1 of matchingProcesses
			delay 0.1
		end repeat
	end tell

	error "Find My did not open."
end waitForFindMyProcess

on run argv
	if (count of argv) is less than 2 then error "Usage: findmy_play_sound.applescript <devices|items> <exact title> [--dry-run|--menu-only|--play] [occurrence]"

	set itemKind to item 1 of argv
	set targetTitle to item 2 of argv
	set runMode to "--dry-run"
	set requestedOccurrence to missing value

	if (count of argv) is greater than or equal to 3 then set runMode to item 3 of argv
	if (count of argv) is greater than or equal to 4 then
		try
			set requestedOccurrence to (item 4 of argv) as integer
		on error
			error "Occurrence must be a positive integer."
		end try
		if requestedOccurrence is less than 1 then error "Occurrence must be a positive integer."
	end if

	if itemKind is "devices" then
		set tabTitle to "Devices"
	else if itemKind is "items" then
		set tabTitle to "Items"
	else
		error "First argument must be either devices or items."
	end if

	if runMode is not "--dry-run" and runMode is not "--menu-only" and runMode is not "--play" then
		error "Mode must be --dry-run, --menu-only, or --play."
	end if

	do shell script "/usr/bin/open -b com.apple.findmy"
	set findMyProcess to my waitForFindMyProcess()

	tell application "System Events"
		set frontmost of findMyProcess to true
		repeat 30 times
			if (count of windows of findMyProcess) is greater than 0 then exit repeat
			delay 0.1
		end repeat
		if (count of windows of findMyProcess) is 0 then error "Find My has no open window."
		set findMyWindow to front window of findMyProcess
	end tell

	set tabMatches to my matchingElements(findMyWindow, "AXRadioButton", tabTitle)
	if (count of tabMatches) is not 1 then error "Could not uniquely identify the Find My " & tabTitle & " tab."
	my clickElementCenter(item 1 of tabMatches)
	delay 0.5

	-- Reacquire the window and its controls because switching tabs rebuilds the
	-- accessibility hierarchy.
	tell application "System Events"
		set findMyWindow to front window of findMyProcess
	end tell
	set titleMatches to my matchingListTitles(findMyWindow, targetTitle)
	set matchCount to count of titleMatches

	if matchCount is 0 then error "No " & itemKind & " entry exactly titled “" & targetTitle & "” was found."

	if requestedOccurrence is missing value then
		if matchCount is greater than 1 then error "Found " & matchCount & " entries exactly titled “" & targetTitle & "”. Supply a 1-based occurrence to disambiguate."
		set selectedOccurrence to 1
	else
		if requestedOccurrence is greater than matchCount then error "Requested occurrence " & requestedOccurrence & ", but only " & matchCount & " exact matches were found."
		set selectedOccurrence to requestedOccurrence
	end if

	if runMode is "--dry-run" then
		return "Found " & matchCount & " exact " & itemKind & " match(es) for “" & targetTitle & "”; selected occurrence " & selectedOccurrence & ". No action taken."
	end if

	set targetElement to item selectedOccurrence of titleMatches
	my clickElementCenter(targetElement)
	delay 0.75

	tell application "System Events"
		set findMyWindow to front window of findMyProcess
	end tell
	set selectedCardMatches to my matchingMapCardTitles(findMyWindow, targetTitle)
	if (count of selectedCardMatches) is 0 then error "Find My did not select the " & itemKind & " entry “" & targetTitle & "”."

	-- Find My 26 advertises AXShowMenu on list entries, but invoking it neither
	-- opens a menu nor leaves the underlying window reference valid. Use the
	-- selected map card's More Info button instead.
	tell application "System Events"
		set findMyWindow to front window of findMyProcess
	end tell
	set infoMatches to my matchingElements(findMyWindow, "AXButton", "More Info")
	if (count of infoMatches) is 0 then set infoMatches to my matchingInfoButtons(findMyWindow)
	if (count of infoMatches) is 0 then error "The selected entry did not expose a More Info button."

	-- Catalyst exposes the same physical More Info button multiple times in
	-- "entire contents." The duplicates share one position and invoke the same
	-- control. Catalyst ignores its advertised AXPress action, so post a real
	-- mouse event at the current accessibility-reported frame.
	my clickElementCenter(item 1 of infoMatches)
	delay 1

	tell application "System Events"
		set findMyWindow to front window of findMyProcess
	end tell
	set playSoundMatches to my matchingElementsByIdentifier(findMyWindow, "AXButton", "PlaySoundButton")
	if (count of playSoundMatches) is 0 then error "The More Info click did not expose a “Play Sound” control."

	if runMode is "--menu-only" then
		return "Opened the verified detail panel for " & itemKind & " entry “" & targetTitle & "”. No sound requested."
	end if

	my clickElementCenter(item 1 of playSoundMatches)
	return "Requested Play Sound for " & itemKind & " entry “" & targetTitle & "” through its detail panel."
end run
