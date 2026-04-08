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
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    b64_img = data["predictions"][0]["bytesBase64Encoded"]
    return base64.b64decode(b64_img)


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

        token = _get_token()
        ts = int(time.time())
        batch_name = f"image-batch-{ts}"

        predictions_jsonl_lines = []
        success_count = 0
        failure_count = 0

        for idx, job in enumerate(image_jobs):
            key = str(
                job.get('sentence_number') or
                job.get('start_sentence_number') or
                job.get('key') or
                idx + 1
            )
            prompt = job.get('formatted_prompt', '')

            try:
                img_bytes = _generate_one_image(token, project_id, location, prompt)
                b64_img = base64.b64encode(img_bytes).decode('utf-8')

                # Refresh token every 50 images to avoid expiry mid-batch
                if (idx + 1) % 50 == 0:
                    token = _get_token()

                # Write in Gemini-batch-compatible format so generate-video CF can read it
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
                print(f"Generated image {idx + 1}/{len(image_jobs)} (key={key})")
            except Exception as img_err:
                failure_count += 1
                print(f"Failed image {idx + 1}/{len(image_jobs)} (key={key}): {img_err}")
                continue

        if success_count == 0:
            return (jsonify({
                "error": "All image generations failed",
                "failure_count": failure_count,
            }), 500, headers)

        # Upload single prediction JSONL that matches what generate-video expects
        pred_file = f"{prefix}/prediction-{ts}.jsonl".lstrip("/") if prefix else f"images/prediction-{ts}.jsonl"
        blob = bucket.blob(pred_file)
        blob.upload_from_string("\n".join(predictions_jsonl_lines), content_type="application/jsonl")

        return (jsonify({
            "success": True,
            "batch_job_name": batch_name,
            "total_images": len(image_jobs),
            "success_count": success_count,
            "failure_count": failure_count,
            "output_file": f"gs://{bucket_name}/{pred_file}",
        }), 200, headers)

    except Exception as e:
        traceback.print_exc()
        return (jsonify({
            'error': 'Internal server error',
            'details': str(e),
            'traceback': traceback.format_exc()[:2000],
        }), 500, headers)
