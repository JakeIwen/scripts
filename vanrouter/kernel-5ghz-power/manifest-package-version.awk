# Print the version from exactly one well-formed "PACKAGE - VERSION" record.
# package_name must be supplied with -v.
BEGIN {
	if (package_name == "")
		exit 2
}

$1 == package_name {
	count++
	if (NF != 3 || $2 != "-" || $3 == "")
		invalid = 1
	else
		version = $3
}

END {
	if (count != 1 || invalid)
		exit 1
	print version
}
