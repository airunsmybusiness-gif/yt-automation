# Deployment Guide — MindSeam YouTube Automation

Reproduces the pipeline that created youtube.com/watch?v=LOzrFoSHnGA.

## Architecture

```
Railway FastAPI orchestrator
   │
   ├─► Supabase (yt_viral_videos, yt_scripts, yt_audio_files, yt_batch_jobs)
   │
   ├─► Gemini Batch API (TTS audio per sentence)
   │       │
   │       └─► CF1: upload-audio-to-gcs   (GCS: {video_id}/audio/)
   │
   ├─► CF2: image-batch-requests          → Vertex AI batch prediction
   │       │ (cost check: reject > $3)
   │       │ (orchestrator polls with 30-min hard timeout + cancel)
   │       └─► GCS: {video_id}/images/prediction-*.jsonl
   │
   ├─► CF3: generate-video                (FFmpeg render → GCS final.mp4)
   │       (NO zoompan · NO drawtext · PIL captions · ffmpeg -i for duration)
   │
   └─► CF4: upload-video                  (YouTube Data API v3 → private)
```

---

## GCP Setup  (Project: youtube-automation-492419)

### 1. Enable APIs
```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  run.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  youtube.googleapis.com \
  --project youtube-automation-492419
```

### 2. Create Service Account
```bash
gcloud iam service-accounts create yt-automation-sa \
  --display-name "YT Automation Pipeline" \
  --project youtube-automation-492419

SA=yt-automation-sa@youtube-automation-492419.iam.gserviceaccount.com

# Roles needed
for role in \
  roles/storage.objectAdmin \
  roles/aiplatform.user \
  roles/secretmanager.secretAccessor \
  roles/cloudfunctions.invoker; do
  gcloud projects add-iam-policy-binding youtube-automation-492419 \
    --member="serviceAccount:$SA" --role="$role"
done
```

### 3. Download SA Key (for Railway)
```bash
gcloud iam service-accounts keys create sa-key.json \
  --iam-account $SA \
  --project youtube-automation-492419
# Paste contents as GCP_SERVICE_ACCOUNT_JSON on Railway
```

### 4. Create GCS Buckets
```bash
ASSETS_BUCKET=yt-automation-492419-assets
BG_BUCKET=yt-auto-bg-audio-ls

gsutil mb -p youtube-automation-492419 -l US gs://$ASSETS_BUCKET
gsutil mb -p youtube-automation-492419 -l US gs://$BG_BUCKET
```

### 5. Upload Reference Image (stick-figure style)
```bash
# stickfigure.jpeg is the style reference for Vertex AI Imagen
gsutil cp stickfigure.jpeg gs://$ASSETS_BUCKET/reference/stickfigure.jpeg
```

### 6. Upload Background Music
```bash
gsutil cp audio1.mp3 gs://$BG_BUCKET/audio1.mp3
```

### 7. Store YouTube Secrets in Secret Manager
```bash
# YouTube OAuth credentials (from Google Cloud Console OAuth 2.0 client)
echo -n "YOUR_CLIENT_ID"     | gcloud secrets create youtube-client-id     --data-file=- --project youtube-automation-492419
echo -n "YOUR_CLIENT_SECRET" | gcloud secrets create youtube-client-secret  --data-file=- --project youtube-automation-492419
echo -n "YOUR_REFRESH_TOKEN" | gcloud secrets create youtube-refresh-token  --data-file=- --project youtube-automation-492419
echo -n "YOUR_GEMINI_KEY"    | gcloud secrets create gemini-api-key         --data-file=- --project youtube-automation-492419
```

---

## Cloud Functions Deployment

Run from repo root. Use `--gen2` and always include `--update-env-vars`.

### CF1 — Gemini TTS processor (`upload-audio-to-gcs`)
```bash
gcloud functions deploy upload-audio-to-gcs \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source cloud_functions/upload-audio-to-gcs \
  --entry-point upload_audio_to_gcs \
  --trigger-http \
  --allow-unauthenticated=false \
  --memory 512MB \
  --timeout 540s \
  --update-env-vars GEMINI_API_KEY_SECRET=projects/youtube-automation-492419/secrets/gemini-api-key/versions/latest \
  --service-account $SA \
  --project youtube-automation-492419
```

### CF2 — Vertex AI Imagen (`image-batch-requests`)
```bash
gcloud functions deploy image-batch-requests \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source cloud_functions/image-batch-requests \
  --entry-point process_batch_images \
  --trigger-http \
  --allow-unauthenticated=false \
  --memory 512MB \
  --timeout 120s \
  --service-account $SA \
  --project youtube-automation-492419
```

**Cost guard is baked in:** any job estimated over $3 (at $0.04/image) is rejected before submission.
The Railway orchestrator enforces a hard **30-minute** polling timeout and cancels via Vertex AI API if exceeded.

### CF3 — FFmpeg render (`generate-video`)
```bash
gcloud functions deploy generate-video \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source cloud_functions/generate-video \
  --entry-point generate_video \
  --trigger-http \
  --allow-unauthenticated=false \
  --memory 4096MB \
  --cpu 2 \
  --timeout 540s \
  --update-env-vars SUPABASE_URL=https://pohozvmvxlskqbklsosr.supabase.co,SUPABASE_KEY=YOUR_SUPABASE_SERVICE_KEY \
  --service-account $SA \
  --project youtube-automation-492419
```

### CF4 — YouTube upload (`upload-video`)
```bash
gcloud functions deploy upload-video \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source cloud_functions/upload-video \
  --entry-point upload_video \
  --trigger-http \
  --allow-unauthenticated=false \
  --memory 1024MB \
  --timeout 540s \
  --service-account $SA \
  --project youtube-automation-492419
```

### Get CF URLs
```bash
for cf in upload-audio-to-gcs image-batch-requests generate-video upload-video; do
  echo "$cf:"
  gcloud functions describe $cf --gen2 --region us-central1 \
    --project youtube-automation-492419 --format='value(serviceConfig.uri)'
done
```

---

## Railway Deployment

### Environment Variables

Set ALL of these in the Railway service dashboard:

| Variable | Value |
|---|---|
| `SUPABASE_URL` | `https://pohozvmvxlskqbklsosr.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `GEMINI_API_KEY` | Gemini API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GCP_PROJECT_ID` | `youtube-automation-492419` |
| `GCP_SERVICE_ACCOUNT_JSON` | Full JSON string from `sa-key.json` |
| `ASSETS_BUCKET` | `yt-automation-492419-assets` |
| `BG_AUDIO_BUCKET` | `yt-auto-bg-audio-ls` |
| `STICK_FIGURE_GCS_PATH` | `reference/stickfigure.jpeg` |
| `VERTEX_LOCATION` | `us-central1` |
| `IMAGEN_MODEL` | `gemini-3-pro-preview` |
| `CF_TTS_URL` | Cloud Function URL for `upload-audio-to-gcs` |
| `CF_IMAGE_URL` | Cloud Function URL for `image-batch-requests` |
| `CF_RENDER_URL` | Cloud Function URL for `generate-video` |
| `CF_UPLOAD_URL` | Cloud Function URL for `upload-video` |
| `YOUTUBE_CLIENT_ID` | YouTube OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | YouTube OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | YouTube OAuth refresh token |
| `CLAUDE_MODEL` | `claude-opus-4-7` |
| `API_SECRET` | Random secret for `/api/*` endpoints |

### Deploy
```bash
# Railway deploys from GitHub push automatically
git push origin claude/rebuild-youtube-pipeline-aYTXC
```

### nixpacks.toml is already configured
```toml
[phases.setup]
nixPkgs = ["python311", "ffmpeg"]
```

---

## Supabase Schema

Run `schema_part1.sql`, `schema_part2.sql`, `schema_part3.sql` in order in the Supabase SQL Editor (project: pohozvmvxlskqbklsosr).

---

## Pipeline Flow (per video)

1. Video row in `yt_viral_videos` with `status=queued` and `suitable=true`
2. Transcript in `yt_video_transcripts` (populate manually or via scraper)
3. Comments in `yt_comments` (optional, improves script)
4. Agent prompts in `yt_agent_prompts`:
   - `agent3_script_writer` — generates script JSON
   - `image_generator` — generates image prompt per sentence
5. Scheduler picks the video every 2 minutes and runs the pipeline

### Manual trigger (skip scheduler)
```bash
curl -X POST https://YOUR_RAILWAY_URL/api/pipeline/trigger/VIDEO_UUID \
  -H "X-API-Key: YOUR_API_SECRET"
```

---

## Cost Controls

| Guard | Where | Limit |
|---|---|---|
| Image batch cost check | CF2 (`image-batch-requests`) | Reject > $3 |
| Vertex AI job timeout | Railway orchestrator (`vertex_guard.py`) | Cancel after 30 min |
| TTS batch timeout | Railway orchestrator (`gemini_tts_batch.py`) | Fail after 20 min |
| Max sentences per script | `pipeline.py` `MAX_SENTENCES` | 130 |

**Previous $124 billing incident**: caused by Vertex AI batch jobs that never completed.
The `vertex_guard.py` module now cancels any job exceeding 30 minutes via:
```
POST https://{location}-aiplatform.googleapis.com/v1/{job_name}:cancel
```

---

## Key Implementation Notes

- **No zoompan**: removed from all FFmpeg commands (causes OOM + jitter)
- **Duration from `ffmpeg -i` stderr**: `_get_audio_duration_sec()` in CF3
- **No drawtext**: PIL bakes captions into frames in `_bake_caption()` in CF3
- **Always `--update-env-vars`**: include in every `gcloud functions deploy` call
- **Stick-figure style**: `stickfigure.jpeg` passed as `reference_image_base64` to Vertex AI Imagen
- **Hard 30-min Vertex AI timeout**: `vertex_guard.py` → cancel → RuntimeError → video re-queued
