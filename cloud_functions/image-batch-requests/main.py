import functions_framework
import json
import requests
import time
from flask import jsonify
from google.cloud import storage
import google.auth
import google.auth.transport.requests

@functions_framework.http
def process_batch_images(request):
    if request.method == 'OPTIONS':
        headers = {'Access-Control-Allow-Origin': '*','Access-Control-Allow-Methods': 'POST','Access-Control-Allow-Headers': 'Content-Type','Access-Control-Max-Age': '3600'}
        return ('', 204, headers)
    headers = {'Access-Control-Allow-Origin': '*'}
    try:
        request_json = request.get_json(silent=True)
        if not request_json:
            return (jsonify({'error': 'No JSON data received'}), 400, headers)
        image_jobs = request_json.get('image_jobs', [])
        reference_image_base64 = request_json.get('reference_image_base64')
        model = request_json.get('model', 'gemini-3-pro-preview')
        project_id = request_json.get('project_id')
        location = request_json.get('location', 'global')
        input_bucket = request_json.get('input_bucket')
        output_bucket = request_json.get('output_bucket')
        if not all([image_jobs, reference_image_base64, project_id, input_bucket, output_bucket]):
            return (jsonify({'error': 'Missing required fields'}), 400, headers)
        lines = []
        for index, job in enumerate(image_jobs):
            sentence_num = job.get('sentence_number') or job.get('start_sentence_number') or job.get('key') or job.get('id') or index + 1
            entry = {"key": str(sentence_num),"request": {"contents": [{"role": "user","parts": [{"text": job["formatted_prompt"]},{"inline_data": {"mime_type": "image/jpeg","data": reference_image_base64}}]}],"generation_config": {"response_modalities": ["IMAGE"],"image_config": {"aspect_ratio": "16:9","image_size": "2K"}}}}
            lines.append(json.dumps(entry))
        jsonl_content = "\n".join(lines)
        used_keys = [job.get('sentence_number') or job.get('start_sentence_number') or job.get('key') or job.get('id') or idx + 1 for idx, job in enumerate(image_jobs)]
        storage_client = storage.Client(project=project_id)
        parts = input_bucket.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        ts = int(time.time())
        file_name = f"{prefix}batch-image-{ts}.jsonl" if prefix else f"batch-image-{ts}.jsonl"
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        blob.upload_from_string(jsonl_content, content_type="application/jsonl")
        input_uri = f"gs://{bucket_name}/{file_name}"
        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        token = credentials.token
        if location == "global":
            endpoint = f"https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/batchPredictionJobs"
        else:
            endpoint = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/batchPredictionJobs"
        payload = {"displayName": f"image-batch-{ts}","model": f"publishers/google/models/{model}","inputConfig": {"instancesFormat": "jsonl","gcsSource": {"uris": [input_uri]}},"outputConfig": {"predictionsFormat": "jsonl","gcsDestination": {"outputUriPrefix": output_bucket}}}
        res = requests.post(endpoint, headers={"Authorization": f"Bearer {token}","Content-Type": "application/json"}, json=payload)
        if res.status_code not in (200, 201):
            return (jsonify({"error": "Vertex AI API Error","status_code": res.status_code,"details": res.text}), 500, headers)
        job_response = res.json()
        job_name = job_response.get("name")
        return (jsonify({"success": True,"batch_job_name": job_name,"input_uri": input_uri,"total_images": len(image_jobs)}), 200, headers)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return (jsonify({"error": "Internal server error", "details": str(e)}), 500, headers)
