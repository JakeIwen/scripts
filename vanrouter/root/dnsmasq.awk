#!/usr/bin/awk -f

BEGIN {
  OFS = ",";
}

$5 == "query[A]" {
  time = mktime( \
    sprintf("%04d %02d %02d %s\n", \
      strftime("%Y", systime()), \
      (match("JanFebMarAprMayJunJulAugSepOctNovDec",$1)+2)/3, \
      $2, \
      gensub(":", " ", "g", $3) \
    ) \
  );
  query = $6;
  host = $8;
  print time, host, query;
}
