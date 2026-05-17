"""
Cloud Function: image-batch-requests
Submits a Vertex AI batch prediction job for image generation.

COST SAFETY:
  - Estimates cost before submitting (PRICE_PER_IMAGE_USD × count)
  - Rejects any job estimated over MAX_JOB_COST_USD ($3)
  - Logs cost estimate to Cloud Logging on every call

BILLING INCIDENT PREVENTION:
  The Railway orchestrator (vertex_guard.py) enforces a hard 30-minute
  timeout and cancels via API if the job exceeds it.
"""
import json
import time
import functions_framework
import requests
from flask import jsonify
from google.cloud import storage
import google.auth
import google.auth.transport.requests

# ── Cost guard constants ─────────────────────────────────────────────────────
PRICE_PER_IMAGE_USD = 0.04   # Imagen 3 standard quality, per image
MAX_JOB_COST_USD = 3.00      # Hard reject above this threshold
# ────────────────────────────────────────────────────────────────────────────


@functions_framework.http
def process_batch_images(request):
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)

    headers = {"Access-Control-Allow-Origin": "*"}

    try:
        request_json = request.get_json(silent=True)
        if not request_json:
            return (jsonify({"error": "No JSON data received"}), 400, headers)

        image_jobs = request_json.get("image_jobs", [])
        reference_image_base64 = request_json.get("reference_image_base64")
        model = request_json.get("model", "gemini-3-pro-preview")
        project_id = request_json.get("project_id")
        location = request_json.get("location", "us-central1")
        input_bucket = request_json.get("input_bucket")
        output_bucket = request_json.get("output_bucket")

        if not all([image_jobs, reference_image_base64, project_id, input_bucket, output_bucket]):
            return (jsonify({"error": "Missing required fields"}), 400, headers)

        # ── COST GUARD: estimate and reject if over $3 ───────────────────────
        num_images = len(image_jobs)
        estimated_cost_usd = num_images * PRICE_PER_IMAGE_USD
        print(
            f"[COST] Estimating {num_images} images × ${PRICE_PER_IMAGE_USD:.3f} "
            f"= ${estimated_cost_usd:.2f} (limit ${MAX_JOB_COST_USD:.2f})"
        )
        if estimated_cost_usd > MAX_JOB_COST_USD:
            msg = (
                f"Estimated job cost ${estimated_cost_usd:.2f} exceeds "
                f"${MAX_JOB_COST_USD:.2f} limit ({num_images} images). "
                "Reduce image count or raise MAX_JOB_COST_USD."
            )
            print(f"[COST] REJECTED — {msg}")
            return (jsonify({"error": "cost_limit_exceeded", "details": msg,
                             "estimated_cost_usd": estimated_cost_usd,
                             "limit_usd": MAX_JOB_COST_USD,
                             "image_count": num_images}), 400, headers)
        # ────────────────────────────────────────────────────────────────────

        # Build JSONL batch input
        lines = []
        for index, job in enumerate(image_jobs):
            sentence_num = (
                job.get("sentence_number")
                or job.get("start_sentence_number")
                or job.get("key")
                or job.get("id")
                or index + 1
            )
            entry = {
                "key": str(sentence_num),
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": job["formatted_prompt"]},
                                {
                                    "inline_data": {
                                        "mime_type": "image/jpeg",
                                        "data": reference_image_base64,
                                    }
                                },
                            ],
                        }
                    ],
                    "generation_config": {
                        "response_modalities": ["IMAGE"],
                        "image_config": {
                            "aspect_ratio": "16:9",
                            "image_size": "2K",
                        },
                    },
                },
            }
            lines.append(json.dumps(entry))

        jsonl_content = "\n".join(lines)

        # Upload JSONL to GCS
        storage_client = storage.Client(project=project_id)
        parts = input_bucket.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        ts = int(time.time())
        file_name = (
            f"{prefix}batch-image-{ts}.jsonl" if prefix else f"batch-image-{ts}.jsonl"
        )
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        blob.upload_from_string(jsonl_content, content_type="application/jsonl")
        input_uri = f"gs://{bucket_name}/{file_name}"

        # Get GCP access token
        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        token = credentials.token

        # Submit Vertex AI batch prediction job
        if location == "global":
            endpoint = (
                f"https://aiplatform.googleapis.com/v1/projects/{project_id}"
                f"/locations/{location}/batchPredictionJobs"
            )
        else:
            endpoint = (
                f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
                f"/locations/{location}/batchPredictionJobs"
            )

        payload = {
            "displayName": f"image-batch-{ts}",
            "model": f"publishers/google/models/{model}",
            "inputConfig": {
                "instancesFormat": "jsonl",
                "gcsSource": {"uris": [input_uri]},
            },
            "outputConfig": {
                "predictionsFormat": "jsonl",
                "gcsDestination": {"outputUriPrefix": output_bucket},
            },
        }

        res = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        if res.status_code not in (200, 201):
            return (
                jsonify({
                    "error": "Vertex AI API Error",
                    "status_code": res.status_code,
                    "details": res.text,
                }),
                500,
                headers,
            )

        job_response = res.json()
        job_name = job_response.get("name")
        print(f"[VERTEX] Submitted batch job: {job_name}")

        return (
            jsonify({
                "success": True,
                "batch_job_name": job_name,
                "input_uri": input_uri,
                "total_images": num_images,
                "estimated_cost_usd": estimated_cost_usd,
            }),
            200,
            headers,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return (jsonify({"error": "Internal server error", "details": str(e)}), 500, headers)
