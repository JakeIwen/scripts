# Batch the original awk windows without changing either provider's locale.
# T begins a message, L prefixes each input line, and E ends it. Prefixing
# lines avoids reserving a character that could occur in conversation text.
function is_continuation(byte) {
    return byte >= "\200" && byte < "\300"
}
function window(text, first, last,    result) {
    if (first < 1) first = 1
    if (last > length(text)) last = length(text)
    if (widen == "true") {
        while (first > 1 && is_continuation(substr(text, first, 1))) first--
        while (last < length(text) && is_continuation(substr(text, last + 1, 1))) last++
    }
    result = substr(text, first, last - first + 1)
    if (first > 1) result = "..." result
    if (last < length(text)) result = result "..."
    return result
}
function excerpt(    position, text) {
    text = flattened
    gsub(/[[:space:]]+/, " ", text)
    position = index(tolower(text), tolower(query))
    if (!position) return ""
    return window(text, position - 50, position + length(query) + 49)
}
function clipped(text, position,    first) {
    if (length(text) <= 300) return text
    first = position > 100 ? position - 100 : 1
    return window(text, first, first + 299)
}
function context_range(first, last,    line) {
    if (context != "") context = context "--\n"
    for (line = first; line <= last; line++)
        context = context clipped(lines[line], positions[line]) "\n"
}
function render_context(    line, first, last, start, end) {
    context = ""
    start = end = 0
    for (line = 1; line <= number; line++) {
        if (!positions[line]) continue
        first = line - before
        if (first < 1) first = 1
        last = line + after
        if (last > number) last = number
        if (end && first > end + 1) {
            context_range(start, end)
            start = 0
        }
        if (!start) start = first
        end = last
    }
    if (!end) return excerpt()
    context_range(start, end)
    return substr(context, 1, length(context) - 1)
}
BEGIN { context_query = tolower(ENVIRON["SESSION_SEARCH_CONTEXT_QUERY"]) }
$0 == "T" {
    flattened = ""
    number = 0
    next
}
substr($0, 1, 1) == "L" {
    text = substr($0, 2)
    if (flattened != "") flattened = flattened " "
    flattened = flattened text
    if (requested == "true") {
        sub(/\r$/, "", text)
        lines[++number] = text
        positions[number] = index(tolower(text), context_query)
    }
    next
}
$0 == "E" {
    result = requested == "true" ? render_context() : excerpt()
    printf "%s%c", result, 0
}
