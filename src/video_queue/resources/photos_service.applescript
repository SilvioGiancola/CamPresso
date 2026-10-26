on run {input, parameters}
    try
        tell application id "com.apple.Photos"
            set chosenItems to get selection
            if (count of chosenItems) is 0 then error "Select a video in Photos first."
            if (count of chosenItems) is greater than 500 then error "Select no more than 500 videos at a time."
            set identifiers to {}
            repeat with chosenItem in chosenItems
                -- Loop variables refer to list slots. Resolve the media item,
                -- then explicitly ask Photos for its ID before coercing it.
                set selectedMedia to contents of chosenItem
                set selectedIdentifier to get id of selectedMedia
                copy (selectedIdentifier as text) to end of identifiers
            end repeat
        end tell
        set appData to (POSIX path of (path to home folder)) & "Library/Application Support/Video Queue/"
        set commandText to (quoted form of (appData & "venv/bin/python")) & " -m video_queue.photos_service"
        repeat with identifierText in identifiers
            set commandText to commandText & " " & (quoted form of ((contents of identifierText) as text))
        end repeat
        do shell script commandText
    on error errorText number errorNumber
        if errorNumber is not -128 then display dialog errorText with title "Convert video to 1080p" buttons {"OK"} default button "OK" with icon caution
    end try
    return input
end run
