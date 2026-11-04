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

# Each local result's tool and id, NUL-terminated, for the fork lookup.
def session_search_index_ids:
    inputs | select((.host // "") == "") | [.tool, .id] |
    map(tostring | gsub("\u0000"; "") + "\u0000") | add;

# Index rows can contain several matches for a session; keep their total and
# newest matching message, but order sessions by activity like the grep scan.
def session_search_index_sessions:
    group_by([.provider, .id]) |
    map(sort_by(.matched_at, .modified) | last + {count: (map(.count) | add)}) |
    sort_by(-.modified, .provider, .id)[];

# NUL-terminated fields for the shell to label and print: tool, cwd, id, name,
# mtime (empty when unknown), count and excerpt. Ungrouped rows keep the index's
# newest-match order; directory roll-ups retain their existing print order.
def session_search_index_fields($group; $dirs):
    [inputs | session_search_index_record] |
    if $dirs then session_search_group(.[]; .id)
    elif $group then session_search_index_sessions
    else .[] end |
    [.provider, .cwd, .id, .name, (if .mtime == null then "" else .modified end),
     .count, .context] |
    # NUL ends each field, so no field may contain one.
    map(tostring | gsub("\u0000"; "") + "\u0000") | add;
