# loolander-data

The toilet data behind the LooLander app.

Once a month this repository downloads the whole of OpenStreetMap, keeps only
the toilets, chops them into small files by location, and uploads them to
Cloudflare R2. The app then reads those files directly. There is no server to
run, nothing to keep alive, and nothing that falls over at three in the morning.

**Your time after setup: none.** It runs itself and emails you only if a run
fails.

---

## Why this exists

Version one of the app asked Nominatim — OpenStreetMap's free public search —
for every lookup. That service handles roughly one request per second *for the
whole world*, and its rules forbid apps that lean on it. Somewhere between a
hundred and a thousand real users, LooLander would simply stop finding
toilets. Not with an error you could debug: with silence.

Owning the data fixes that permanently, and costs about nothing.

---

## How it works

```
Geofabrik (OpenStreetMap, refreshed daily)
    |   one country-sized file at a time, deleted after use
    v
osmium tags-filter        keep only amenity=toilets
    |
    v
scripts/build_tiles.py    chop into 1-degree squares, gzip each one
    |
    v
Cloudflare R2             static files, no egress charge
    |
    v
the phone                 fetches the 1-4 squares around you
```

A one-degree square is about 111 km tall and, at Danish latitudes, about 64 km
wide. A search with a 50 km radius touches at most a handful of them.

### What ends up in the bucket

```
v1/manifest.json          which squares exist, how many toilets in each, when built
v1/cells/55/12.json       one square: Copenhagen
v1/cells/51/-1.json       one square: London
```

Files are stored gzipped and served with `Content-Encoding: gzip`, so they are
named `.json` even though the bytes on disk are compressed. The phone's HTTP
client unpacks them without being asked. Do not rename them to `.json.gz`.

---

## One-time setup

About an hour, almost all of it clicking. Do it in this order.

### 1. Put loolander.com on Cloudflare

Free. Sign up at [dash.cloudflare.com](https://dash.cloudflare.com), choose
**Add a domain**, enter `loolander.com`, pick the **Free** plan. Cloudflare
gives you two nameservers; go to wherever you bought the domain and replace the
existing nameservers with those two. It takes a few minutes to a few hours to
take effect.

This step is not optional busywork. It is what lets you serve the data from
`data.loolander.com` instead of R2's development URL, which Cloudflare rate
limits and explicitly says not to use in production. It also puts the files in
Cloudflare's cache, so most requests never reach R2 at all.

### 2. Make the bucket

In the Cloudflare dashboard: **R2** → **Create bucket** → name it
`loolander-data` → **Create**.

Then open the bucket → **Settings** → **Custom Domains** → **+ Add** → enter
`data.loolander.com`. Cloudflare adds the DNS record for you. Ignore the
**Public Development URL** block below it: that is the `r2.dev` address
Cloudflare rate limits and tells you not to ship with.

Set the **jurisdiction** to European Union when you create the bucket. It keeps
the data in the EU, which is one less thing to explain in a privacy policy.

Check it worked by visiting `https://data.loolander.com/v1/manifest.json` — you
should get a "not found" style error rather than a connection failure. Nothing
is uploaded yet.

### 3. Make an API token

**R2** → **API** → **Manage API tokens** → **Create API token**.

- Permission: **Object Read & Write**
- Scope it to the `loolander-data` bucket only

It shows you two values **once**. Copy both somewhere safe now:

- Access Key ID
- Secret Access Key

You do not need the account ID separately — the S3 API address from the
bucket's Settings page already contains it.

If you lose them, delete the token and make a new one. Do not paste them into a
chat, an email, or a file in the repository.

### 4. Put this folder on GitHub

Create a **public** repository called `loolander-data`. Public matters:
GitHub Actions minutes are free and unlimited on public repositories, and this
job uses two to four hours a month. There is nothing secret in the code — the
keys live in GitHub's encrypted secrets, never in a file.

Upload everything in this folder to it.

**One file needs care.** `refresh-data.yml` sits in the root of this folder,
but on GitHub it has to live at `.github/workflows/refresh-data.yml`. Windows
Explorer will not let you create a folder whose name starts with a dot, so do
it on GitHub instead: in your new repository choose **Add file** → **Create new
file**, type `.github/workflows/refresh-data.yml` as the filename — GitHub
creates the folders as you type the slashes — then paste in the contents of
`refresh-data.yml` and commit. Delete the copy in the root afterwards.

### 5. Paste in the secrets

In the repository: **Settings** → **Secrets and variables** → **Actions**.

Under **Secrets**, add three:

| Name | Value |
|---|---|
| `R2_S3_API` | the bucket's **S3 API** address, copied whole |
| `R2_ACCESS_KEY_ID` | from step 3 |
| `R2_SECRET_ACCESS_KEY` | from step 3 |

`R2_S3_API` is the value in the **S3 API** box on your bucket's Settings page.
Copy it exactly as shown, bucket name on the end and all — the workflow splits
it into the endpoint and the bucket name itself. Copying it beats typing out
the account ID, because a bucket created with an EU jurisdiction has an extra
`.eu` in its address and getting that wrong fails every upload with a
misleading permissions error.

Under **Variables** (the other tab), add one:

| Name | Value |
|---|---|
| `DATA_BASE_URL` | `https://data.loolander.com` |

### 6. Test it on Denmark first

**Actions** → **Refresh toilet data** → **Run workflow**, and set:

- Only these regions: `denmark`
- Dry run: leave unticked

Two to three minutes. It downloads Denmark, extracts the toilets, builds the
squares and checks they look sane — but does **not** upload, because a
single-country run must never overwrite the worldwide dataset.

Read the summary at the bottom of the run. You should see somewhere in the
low thousands of toilets across a handful of squares. If you see zero, or a
red X, stop and read the log rather than running the full job.

### 7. Run the world

**Run workflow** again with both inputs left blank.

Two to four hours. When it finishes, open
`https://data.loolander.com/v1/manifest.json` in a browser — you should see the
full count. That URL is what goes into the app.

After this, it runs on the 1st of every month on its own.

---

## What it costs

Nothing, at any size you are likely to reach.

| | |
|---|---|
| GitHub Actions | free, unlimited on a public repository |
| R2 storage | a few hundred MB against a 10 GB free allowance |
| R2 reads | free up to 10 million a month; a million app downloads uses about 1.2 million |
| Bandwidth | R2 never charges for egress |

The first bill would arrive somewhere past ten million monthly active users,
and would be single-digit dollars.

---

## When it breaks

GitHub emails you when a scheduled run fails. It will not have touched the live
data — the checks run before the upload, and the manifest is written last, so
the app carries on serving last month's toilets. Stale data is a much smaller
problem than missing data, and the app shows how old its data is.

**"only N usable regions in index-v1.json"** — Geofabrik changed the format of
its region list. The fix is in `scripts/regions.py`.

**"the new dataset lost N% of its toilets"** — a region failed to download
quietly. Look through the extract log for a region with a suspiciously low
count, then re-run. This check exists precisely so a bad run cannot overwrite a
good one.

**"only N toilets, expected at least 100000"** — the same thing, but worse.
Almost always a Geofabrik outage. Wait a day and re-run.

**Uploads fail with a 501** — R2 rejected the AWS CLI's checksum headers. The
two `AWS_*_CHECKSUM_*` lines in the workflow prevent this; check they are still
there.

**Runs out of disk** — a single region grew past the 3.5 GB limit. Lower
`--max-gb` in the "Work out which regions" step and it will split that region
into smaller pieces.

**The plan looks much bigger than the world** — Geofabrik publishes bundles
like `dach` and `us` alongside the countries and states they already contain,
as siblings rather than parents, so walking the tree cannot see the overlap.
`BUNDLES` in `scripts/regions.py` lists the ones worth skipping and what has to
be present before each is dropped. A bundle whose contents are not all covered
is kept, so a reorganisation at Geofabrik costs bandwidth rather than losing a
country. The log says which were dropped and how much that saved.

---

## Running it on your own machine

You need `osmium-tool` and Python 3.9+. On Windows that means WSL or Docker,
which is why this runs on GitHub instead.

```bash
python3 scripts/regions.py --only denmark > plan.tsv
bash scripts/extract.sh < plan.tsv
python3 scripts/build_tiles.py toilets.geojsonseq --out out
python3 scripts/check_sane.py --new out/v1/manifest.json --min-count 100
```

Tests, which need nothing but Python:

```bash
python3 test/test_build_tiles.py
python3 test/test_regions.py
```

---

## Data licence

OpenStreetMap data is published under the
[Open Database Licence](https://www.openstreetmap.org/copyright). You may use
it commercially, including in a paid app, but the app **must** credit
OpenStreetMap visibly. LooLander does this on the detail sheet. Do not remove
that credit — the licence is the only reason this project can exist.
