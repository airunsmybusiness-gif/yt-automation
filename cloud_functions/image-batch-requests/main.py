import functions_framework
import json
import time
import traceback
import requests
from flask import jsonify
from google.cloud import storage
import google.auth
import google.auth.transport.requests


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
        reference_image_base64 = body.get('reference_image_base64')
        model = body.get('model', 'gemini-3-pro-preview')
        project_id = body.get('project_id')
        location = body.get('location', 'global')
        input_bucket = body.get('input_bucket')
        output_bucket = body.get('output_bucket')

        missing = [k for k, v in {
            'image_jobs': image_jobs,
            'reference_image_base64': reference_image_base64,
            'project_id': project_id,
            'input_bucket': input_bucket,
            'output_bucket': output_bucket,
        }.items() if not v]
        if missing:
            return (jsonify({'error': f'Missing fields: {missing}'}), 400, headers)

        lines = []
        for idx, job in enumerate(image_jobs):
            key = (job.get('sentence_number') or
                   job.get('start_sentence_number') or
                   job.get('key') or
                   job.get('id') or
                   idx + 1)
            lines.append(json.dumps({
                "key": str(key),
                "request": {
                    "contents": [{
                        "role": "user",
                        "parts": [
                            {"text": job["formatted_prompt"]},
                            {"inline_data": {
                                "mime_type": "image/jpeg",
                                "data": reference_image_base64,
                            }},
                        ],
                    }],
                    "generation_config": {
                        "response_modalities": ["IMAGE"],
                        "image_config": {
                            "aspect_ratio": "16:9",
                            "image_size": "2K",
                        },
                    },
                },
            }))
        jsonl_content = "\n".join(lines)

        parts = input_bucket.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            try:
                bucket = storage_client.create_bucket(bucket_name, location="us-central1")
                print(f"Created bucket: {bucket_name}")
            except Exception as create_err:
                return (jsonify({
                    'error': 'Failed to create bucket',
                    'bucket': bucket_name,
                    'details': str(create_err),
                }), 500, headers)

        ts = int(time.time())
        file_name = f"{prefix}/batch-image-{ts}.jsonl" if prefix else f"batch-image-{ts}.jsonl"
        file_name = file_name.lstrip("/")

        blob = bucket.blob(file_name)
        try:
            blob.upload_from_string(jsonl_content, content_type="application/jsonl")
        except Exception as up_err:
            return (jsonify({
                'error': 'Failed to upload JSONL',
                'bucket': bucket_name,
                'file': file_name,
                'details': str(up_err),
            }), 500, headers)

        input_uri = f"gs://{bucket_name}/{file_name}"

        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        token = credentials.token

        endpoint_host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        endpoint = f"https://{endpoint_host}/v1/projects/{project_id}/locations/{location}/batchPredictionJobs"

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
            return (jsonify({
                "error": "Vertex AI API error",
                "status_code": res.status_code,
                "details": res.text[:1000],
                "endpoint": endpoint,
            }), 500, headers)

        job_response = res.json()
        return (jsonify({
            "success": True,
            "batch_job_name": job_response.get("name"),
            "input_uri": input_uri,
            "total_images": len(image_jobs),
        }), 200, headers)

    except Exception as e:
        traceback.print_exc()
        return (jsonify({
            'error': 'Internal server error',
            'details': str(e),
            'traceback': traceback.format_exc()[:2000],
        }), 500, headers)
