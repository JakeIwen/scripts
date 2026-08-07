# Resolve installed APK names to the package selectors used by OpenWrt's
# build configuration. Packages with ABI_VERSION are installed with that
# version appended, but CONFIG_PACKAGE_* continues to use the base name.

function report_error(message) {
	print "resolve-package-seed: " message > "/dev/stderr"
	failed = 1
}

function add_alias(alias, package) {
	if ((alias in package_for) && package_for[alias] != package) {
		report_error("ambiguous package metadata for " alias ": " \
			package_for[alias] " and " package)
		return
	}
	package_for[alias] = package
}

function register_package(abi_separator, installed_name) {
	if (metadata_package == "")
		return
	add_alias(metadata_package, metadata_package)
	if (metadata_abi != "" && metadata_package !~ /^kmod-/) {
		abi_separator = metadata_package ~ /[0-9]$/ ? "-" : ""
		installed_name = metadata_package abi_separator metadata_abi
		add_alias(installed_name, metadata_package)
	}
	metadata_package = ""
	metadata_abi = ""
}

FILENAME == metadata_file {
	if ($0 ~ /^Package:[[:space:]]*/) {
		register_package()
		metadata_package = $0
		sub(/^Package:[[:space:]]*/, "", metadata_package)
	} else if ($0 ~ /^ABI-Version:[[:space:]]*/) {
		metadata_abi = $0
		sub(/^ABI-Version:[[:space:]]*/, "", metadata_abi)
	}
	next
}

!metadata_complete {
	register_package()
	metadata_complete = 1
}

{
	seed = $0
	sub(/#.*/, "", seed)
	gsub(/[[:space:]]/, "", seed)
	if (seed == "")
		next
	if (seed !~ /^[A-Za-z0-9+_.-]+$/) {
		report_error("unsafe package name: " seed)
		next
	}
	if (seed == "kernel") {
		if (map_file != "")
			print seed "\t<implicit-target-package>" > map_file
		print "resolve-package-seed: kernel is provided implicitly by the target" \
			> "/dev/stderr"
		next
	}
	if (!(seed in package_for)) {
		report_error("installed package has no selector in pinned metadata: " seed)
		next
	}
	resolved = package_for[seed]
	if (map_file != "")
		print seed "\t" resolved > map_file
	if (seed != resolved)
		print "resolve-package-seed: mapped " seed " -> " resolved \
			> "/dev/stderr"
	if (!(resolved in emitted)) {
		print resolved
		emitted[resolved] = 1
	}
}

END {
	if (!metadata_complete)
		register_package()
	if (failed)
		exit 1
}
