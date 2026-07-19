BEGIN {
    OFS = "|"
    reset_cell()
}

function reset_cell() {
    essid = ""
    quality = 0
    signal = ""
    frequency = ""
    channel = ""
    address = ""
    wep = 0
    wpa = 0
    have_cell = 0
}

function emit_cell(security) {
    if (!have_cell || essid == "") return
    if (wpa) security = "wpa"
    else if (wep) security = "wep"
    else security = "none"
    print quality, essid, security, frequency, channel, address, signal
}

/Cell [0-9]+ - Address:/ {
    emit_cell()
    reset_cell()
    have_cell = 1
    address = $0
    sub(/^.*Address:[[:space:]]*/, "", address)
    sub(/[[:space:]]*$/, "", address)
    next
}

/ESSID:/ {
    essid = $0
    sub(/^.*ESSID:/, "", essid)
    sub(/^[[:space:]]*"/, "", essid)
    sub(/"[[:space:]]*$/, "", essid)
}

/Quality[=:]/ {
    quality_text = $0
    sub(/^.*Quality[=:]/, "", quality_text)
    split(quality_text, quality_parts, /[[:space:]]+/)
    split(quality_parts[1], quality_values, "/")
    if (quality_values[2] > 0) quality = int(quality_values[1] / quality_values[2] * 100)
    else quality = quality_values[1] + 0
}

/Signal level[=:]/ {
    signal_text = $0
    sub(/^.*Signal level[=:][[:space:]]*/, "", signal_text)
    split(signal_text, signal_parts, /[[:space:]]+/)
    signal = signal_parts[1]
}

/Frequency[=:]/ {
    frequency_text = $0
    sub(/^.*Frequency[=:][[:space:]]*/, "", frequency_text)
    split(frequency_text, frequency_parts, /[[:space:]]+/)
    frequency = int(frequency_parts[1] * 1000 + 0.5)
    if ($0 ~ /\(Channel [0-9]+\)/) {
        channel_text = $0
        sub(/^.*\(Channel[[:space:]]*/, "", channel_text)
        sub(/\).*$/, "", channel_text)
        channel = channel_text + 0
    }
}

/Channel[=:][[:space:]]*[0-9]+/ {
    channel_text = $0
    sub(/^.*Channel[=:][[:space:]]*/, "", channel_text)
    sub(/[^0-9].*$/, "", channel_text)
    channel = channel_text + 0
}

/Encryption key:(o|O)n/ { wep = 1 }
/IE:.*(WPA|WPA2|IEEE 802\.11i)/ { wpa = 1 }

END { emit_cell() }
