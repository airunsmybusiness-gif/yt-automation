# Phase 3: TTS + Image Generation — Directive

## Objective
Convert the optimized script (yt_scripts) into per-sentence audio via Gemini TTS batch, and per-sentence images via Vertex AI Imagen batch. Both keyed by sentence_number for downstream FFmpeg sync.

## Inputs
- yt_scripts: sentence_number + sentence_text (ordered)
- yt_agent_prompts: image_generator prompt
- GCS bucket per viral_video_id
- Gemini TTS batch API
- Vertex AI Imagen batch API

## Outputs
- yt_audio_files: one WAV per sentence group, stored in GCS
- yt_image_generation_jobs: one image per sentence, stored in GCS prediction JSONL
- yt_batch_jobs: tracking records for both TTS and image batches

## Flow
### TTS Pipeline
1. Load all sentences from yt_scripts for the video
2. Group sentences into chunks (e.g., 5 sentences per audio file for natural flow)
3. Build JSONL with each chunk as a TTS request (key = start_sentence_number)
4. Upload JSONL to GCS input bucket
5. Submit Gemini TTS batch job
6. Poll job status every 60s (max 10 minutes)
7. On completion: call upload-audio-to-gcs Cloud Function to extract WAV files
8. Save audio file records to yt_audio_files

### Image Pipeline
1. Load all sentences from yt_scripts
2. For each sentence, generate an image prompt using the image_generator agent prompt
3. Get reference image (channel style thumbnail) as base64
4. Build JSONL with each image request (key = sentence_number)
5. Upload JSONL to GCS input bucket
6. Submit Vertex AI Imagen batch job via process_batch_images Cloud Function
7. Save batch job to yt_batch_jobs (status: pending)
8. Batch completion is detected by polling or webhook

## Edge Cases
| Scenario | Handling |
|---|---|
| TTS batch job fails | Retry once, then alert |
| Individual TTS sentence fails | Log, continue with others |
| Image batch partially fails | Log failed keys, continue with available |
| GCS upload fails | Retry 3x with backoff |
| Batch job timeout (>15 min) | Mark as failed, alert |
| Empty script (0 sentences) | Skip, log warning |

## Self-Annealing Rules
- On TTS failure: log ANNEALING, check if Gemini API key is valid
- On image batch partial failure: log missing sentence_numbers for manual review
- On GCS permission error: log ANNEALING, verify service account permissions
