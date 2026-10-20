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

def session_search_record_text:
    "\(.count) matches | \(.header)\n\(.resume)\n\(.context)\n";

# Groups records by tool and directory, in print order: each group is its
# latest record with the group's total count. `session` tells sessions apart.
def session_search_group(records; session):
    reduce records as $record ({};
        # Without a cwd, keep sessions separate instead of inventing a directory.
        ([$record.provider, $record.cwd,
          (if $record.cwd == "" then $record | session else "" end)] | tojson) as $key |
        .[$key] as $previous |
        if $previous == null then .[$key] = $record
        else
            ($previous.count + $record.count) as $count |
            (if [$record.matched_at, $record.modified, ($record | session)] >
                [$previous.matched_at, $previous.modified, ($previous | session)]
             then $record else $previous end) as $latest |
            .[$key] = ($latest + {count: $count})
        end) |
    [.[]] | group_by(.cwd) | sort_by(map(.modified) | max) | .[] |
    sort_by(.provider)[];

def session_search_group_directories:
    session_search_group(inputs; .resume) | session_search_record_text;
