#!/usr/bin/env bash
#
# Download each Geofabrik region in turn, pull out the toilets, throw the
# region away again. Peak disk use is one region at a time, so this runs on a
# small machine even though it reads about 80 GB in total.
#
# Reads the plan (id<TAB>url<TAB>bytes) on stdin, from scripts/regions.py.
# Writes one GeoJSON feature per line to $OUT (default toilets.geojsonseq).

set -euo pipefail

OUT="${OUT:-toilets.geojsonseq}"
WORK="${WORK:-$(mktemp -d)}"
UA="LooLander-data/1.0 (monthly toilet extract; +https://loolander.com)"

mkdir -p "$WORK"
: > "$OUT"

command -v osmium >/dev/null || { echo "osmium is not installed" >&2; exit 1; }

total=0
index=0
plan="$WORK/plan.tsv"
cat > "$plan"
count=$(wc -l < "$plan")

while IFS=$'\t' read -r id url bytes; do
  [ -n "${id:-}" ] || continue
  index=$((index + 1))
  printf '::group::[%d/%d] %s\n' "$index" "$count" "$id"
  started=$SECONDS

  # --retry handles the transient 5xx and reset connections you get when
  # pulling a couple of hundred files in a row.
  curl --fail --location --silent --show-error \
       --retry 4 --retry-delay 10 --retry-all-errors \
       --connect-timeout 30 --max-time 3600 \
       -A "$UA" -o "$WORK/region.osm.pbf" "$url"

  # Keeps nodes and ways tagged amenity=toilets. Without -R it also keeps the
  # nodes those ways are built from, which is what lets the next step work out
  # where a toilet building actually is.
  osmium tags-filter --overwrite \
    -o "$WORK/toilets.osm.pbf" "$WORK/region.osm.pbf" nw/amenity=toilets

  osmium export --overwrite \
    -f geojsonseq -u type_id \
    -o "$WORK/toilets.geojsonseq" "$WORK/toilets.osm.pbf"

  found=$(wc -l < "$WORK/toilets.geojsonseq")
  cat "$WORK/toilets.geojsonseq" >> "$OUT"
  total=$((total + found))

  rm -f "$WORK/region.osm.pbf" "$WORK/toilets.osm.pbf" "$WORK/toilets.geojsonseq"
  printf '%s: %s features in %ds (running total %s)\n' "$id" "$found" "$((SECONDS - started))" "$total"
  printf '::endgroup::\n'
done < "$plan"

echo "extracted $total features from $count regions into $OUT"
if [ "$total" -eq 0 ]; then
  echo "ERROR: nothing extracted - refusing to continue" >&2
  exit 1
fi
