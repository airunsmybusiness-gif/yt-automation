import base64
import functions_framework
import json
import time
import traceback
import requests
from flask import jsonify
from google.cloud import storage
import google.auth
import google.auth.transport.requests


IMAGEN_MODEL = "imagen-3.0-generate-002"
MAX_RETRIES = 3
RETRY_DELAY = 5


def _get_token():
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _generate_one_image(token, project_id, location, prompt):
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/publishers/google/models/{IMAGEN_MODEL}:predict"
    )
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "16:9",
            "safetyFilterLevel": "block_only_high",
            "personGeneration": "allow_adult",
        },
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    preds = data.get("predictions", [])
    if not preds:
        raise RuntimeError(f"No predictions returned: {data}")
    b64_img = preds[0].get("bytesBase64Encoded")
    if not b64_img:
        raise RuntimeError(f"Prediction missing image data: {preds[0]}")
    return base64.b64decode(b64_img)


def _generate_with_retry(token_ref, project_id, location, prompt, key):
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return _generate_one_image(token_ref[0], project_id, location, prompt)
        except Exception as e:
            last_err = e
            print(f"Attempt {attempt + 1}/{MAX_RETRIES} failed for key={key}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                try:
                    token_ref[0] = _get_token()
                except Exception:
                    pass
    raise last_err


def _load_existing_keys(bucket, prefix):
    """Scan existing prediction files and return set of already-generated keys."""
    existing = set()
    for blob in bucket.list_blobs(prefix=f"{prefix}/prediction-" if prefix else "images/prediction-"):
        if not blob.name.endswith(".jsonl"):
            continue
        try:
            content = blob.download_as_text()
            for line in content.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    k = data.get("key")
                    resp = data.get("response", {})
                    parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    has_image = any(
                        p.get("inlineData", {}).get("data") or p.get("inline_data", {}).get("data")
                        for p in parts
                    )
                    if k is not None and has_image:
                        existing.add(str(k))
                except Exception:
                    continue
        except Exception as e:
            print(f"Could not read {blob.name}: {e}")
    return existing


@functions_framework.http
def process_batch_images(request):
    if request.method == 'OPTIONS':
        return ('', 204, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    headers = {'Access-Control-Allow-Origin': '*'}

    try:
        body = request.get_json(silent=True) or {}
        image_jobs = body.get('image_jobs', [])
        project_id = body.get('project_id')
        location = body.get('location', 'us-central1')
        output_bucket = body.get('output_bucket')

        if not all([image_jobs, project_id, output_bucket]):
            return (jsonify({'error': 'Missing required fields'}), 400, headers)

        parts = output_bucket.replace("gs://", "").rstrip("/").split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            bucket = storage_client.create_bucket(bucket_name, location="us-central1")
            print(f"Created bucket: {bucket_name}")

        # Skip keys that already have valid images from previous runs
        existing_keys = _load_existing_keys(bucket, prefix)
        print(f"Found {len(existing_keys)} already-generated images, will skip them")

        token_ref = [_get_token()]
        ts = int(time.time())
        batch_name = f"image-batch-{ts}"

        predictions_jsonl_lines = []
        success_count = 0
        failure_count = 0
        skipped_count = 0
        failed_keys = []

        # Incremental save every N images so nothing is lost if function dies
        CHECKPOINT_EVERY = 20

        def _save_checkpoint(lines, suffix):
            if not lines:
                return
            cp_file = f"{prefix}/prediction-{ts}-{suffix}.jsonl".lstrip("/") if prefix else f"images/prediction-{ts}-{suffix}.jsonl"
            cp_blob = bucket.blob(cp_file)
            cp_blob.upload_from_string("\n".join(lines), content_type="application/jsonl")
            print(f"Checkpoint saved: {cp_file} ({len(lines)} images)")

        for idx, job in enumerate(image_jobs):
            key = str(
                job.get('sentence_number') or
                job.get('start_sentence_number') or
                job.get('key') or
                idx + 1
            )
            prompt = job.get('formatted_prompt', '')

            if key in existing_keys:
                skipped_count += 1
                print(f"Skip {idx + 1}/{len(image_jobs)} (key={key}): already exists")
                continue

            # Refresh token every 50 images
            if idx > 0 and idx % 50 == 0:
                try:
                    token_ref[0] = _get_token()
                except Exception as e:
                    print(f"Token refresh failed: {e}")

            try:
                img_bytes = _generate_with_retry(token_ref, project_id, location, prompt, key)
                b64_img = base64.b64encode(img_bytes).decode('utf-8')
                predictions_jsonl_lines.append(json.dumps({
                    "key": key,
                    "response": {
                        "candidates": [{
                            "content": {
                                "parts": [{
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": b64_img,
                                    }
                                }]
                            }
                        }]
                    }
                }))
                success_count += 1
                print(f"Generated {idx + 1}/{len(image_jobs)} (key={key}) [success={success_count}]")
            except Exception as img_err:
                failure_count += 1
                failed_keys.append(key)
                print(f"FAILED {idx + 1}/{len(image_jobs)} (key={key}): {img_err}")

            # Checkpoint every N successes so we don't lose progress
            if success_count > 0 and success_count % CHECKPOINT_EVERY == 0:
                try:
                    _save_checkpoint(predictions_jsonl_lines, f"cp{success_count}")
                except Exception as e:
                    print(f"Checkpoint failed: {e}")

            # Small delay to avoid rate limits
            time.sleep(0.3)

        # Final save
        if predictions_jsonl_lines:
            pred_file = f"{prefix}/prediction-{ts}.jsonl".lstrip("/") if prefix else f"images/prediction-{ts}.jsonl"
            blob = bucket.blob(pred_file)
            blob.upload_from_string("\n".join(predictions_jsonl_lines), content_type="application/jsonl")
            print(f"Final saved: {pred_file}")

        return (jsonify({
            "success": success_count > 0 or skipped_count > 0,
            "batch_job_name": batch_name,
            "total_images": len(image_jobs),
            "success_count": success_count,
            "skipped_count": skipped_count,
            "failure_count": failure_count,
            "failed_keys": failed_keys[:50],
        }), 200, headers)

    except Exception as e:
        traceback.print_exc()
        return (jsonify({
            'error': 'Internal server error',
            'details': str(e),
            'traceback': traceback.format_exc()[:2000],
        }), 500, headers)
