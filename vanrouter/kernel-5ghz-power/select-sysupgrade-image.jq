.profiles[$profile] as $entry
| if $entry == null then
    error("missing profile: " + $profile)
  elif (($entry.supported_devices // []) | index($board)) == null then
    error("profile does not support board: " + $board)
  else
    $entry
  end
| . as $entry
| [
    $entry.images[]?
    | select(.type == "sysupgrade" and .filesystem == "squashfs")
  ] as $matches
| if ($matches | length) != 1 then
    error("expected exactly one squashfs sysupgrade image")
  else
    $matches[0]
  end
| . as $image
| if (($image.name | type) != "string") or
     ($image.name != ($entry.image_prefix + "-squashfs-sysupgrade.itb")) then
    error("sysupgrade filename does not match the profile image prefix")
  elif (($image.name | test("^[A-Za-z0-9_.+-]+$")) | not) then
    error("unsafe sysupgrade filename")
  elif (($image.sha256 | type) != "string") or
       (($image.sha256 | test("^[0-9a-f]{64}$")) | not) then
    error("invalid sysupgrade SHA-256")
  elif (($image.size | type) != "number") or ($image.size <= 0) then
    error("invalid sysupgrade size")
  else
    [$image.name, $image.sha256, ($image.size | tostring)] | @tsv
  end
