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
    [.[]] | group_by(.cwd) | sort_by(-(map(.modified) | max), .[0].cwd) | .[] |
    sort_by(-.modified, .provider)[];

def session_search_group_directories:
    session_search_group(inputs; .resume) | session_search_record_text;

# Like the grep scripts, name an untitled session after its directory.
def session_search_index_name:
    if .title != "" and .title != "(untitled)" then .title
    elif .cwd == "" then "unknown"
    else .cwd | sub("/$"; "") | split("/") | (last // "") | if . == "" then "/" else . end
    end;

# `llm-switchboard search --json` results, newest match first, as records.
def session_search_index_record:
    ((.mtime // 0) | floor) as $modified |
    {provider: .tool, cwd, id, name: session_search_index_name, mtime, modified: $modified,
     matched_at: (.matched_at // $modified | floor), count, context: (.context // .excerpt)};

# NUL-terminated fields for the shell to label and print: tool, cwd, id, name,
# mtime (empty when unknown), count and excerpt. Keep the index's newest-match
# order, or group newest first like grep mode's -g.
def session_search_index_fields($group):
    [inputs | session_search_index_record] |
    if $group then session_search_group(.[]; .id) else .[] end |
    [.provider, .cwd, .id, .name, (if .mtime == null then "" else .modified end),
     .count, .context] |
    # NUL ends each field, so no field may contain one.
    map(tostring | gsub("\u0000"; "") + "\u0000") | add;
