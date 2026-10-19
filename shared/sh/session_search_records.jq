# Internal records let llmsearch combine results without parsing shell commands.
def session_search_match_output($text):
    if env.SESSION_SEARCH_RECORDS == "1" then
        ({text: $text, timestamp: (.timestamp // null)} | tojson), "\n"
    else $text, "\u0000"
    end;

def session_search_record($provider; $cwd; $modified; $header; $resume; $context):
    {
        provider: $provider, cwd: $cwd, modified: $modified,
        matched_at: ((.timestamp | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)? // $modified),
        count: .count, header: $header, resume: $resume, context: $context
    };

def session_search_group_directories:
    reduce inputs as $record ({};
        # Without a cwd, keep sessions separate instead of inventing a directory.
        ([$record.provider, $record.cwd,
          (if $record.cwd == "" then $record.resume else "" end)] | tojson) as $key |
        .[$key] as $previous |
        if $previous == null then .[$key] = $record
        else
            ($previous.count + $record.count) as $count |
            (if [$record.matched_at, $record.modified, $record.resume] >
                [$previous.matched_at, $previous.modified, $previous.resume]
             then $record else $previous end) as $latest |
            .[$key] = ($latest + {count: $count})
        end) |
    [.[]] | group_by(.cwd) | sort_by(map(.modified) | max) | .[] |
    sort_by(.provider)[] |
    "\(.count) matches | \(.header)\n\(.resume)\n\(.context)\n";
