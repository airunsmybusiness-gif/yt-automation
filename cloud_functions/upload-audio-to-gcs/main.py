import functions_framework
import requests
from google.cloud import storage
from google.cloud import secretmanager
import json
import base64
import struct
import io
from flask import jsonify
import os

def get_api_key():
    secret_name = os.environ.get('GEMINI_API_KEY_SECRET')
    if not secret_name:
        raise ValueError("GEMINI_API_KEY_SECRET environment variable not set")
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": secret_name})
    return response.payload.data.decode('UTF-8')

def pcm_to_wav(pcm_data, sample_rate=24000, channels=1, bits_per_sample=16):
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    wav_buffer = io.BytesIO()
    wav_buffer.write(b'RIFF')
    wav_buffer.write(struct.pack('<I', data_size + 36))
    wav_buffer.write(b'WAVE')
    wav_buffer.write(b'fmt ')
    wav_buffer.write(struct.pack('<I', 16))
    wav_buffer.write(struct.pack('<H', 1))
    wav_buffer.write(struct.pack('<H', channels))
    wav_buffer.write(struct.pack('<I', sample_rate))
    wav_buffer.write(struct.pack('<I', byte_rate))
    wav_buffer.write(struct.pack('<H', block_align))
    wav_buffer.write(struct.pack('<H', bits_per_sample))
    wav_buffer.write(b'data')
    wav_buffer.write(struct.pack('<I', data_size))
    wav_buffer.write(pcm_data)
    return wav_buffer.getvalue()

@functions_framework.http
def upload_audio_to_gcs(request):
    if request.method == 'OPTIONS':
        headers = {'Access-Control-Allow-Origin': '*','Access-Control-Allow-Methods': 'POST','Access-Control-Allow-Headers': 'Content-Type','Access-Control-Max-Age': '3600'}
        return ('', 204, headers)
    headers = {'Access-Control-Allow-Origin': '*'}
    try:
        try:
            api_key = get_api_key()
        except Exception as e:
            return (jsonify({'error': 'Failed to retrieve API key', 'details': str(e)}), 500, headers)
        request_json = request.get_json(silent=True)
        if not request_json:
            return (jsonify({'error': 'No JSON payload provided'}), 400, headers)
        bucket_name = request_json.get('bucket_name')
        file_name = request_json.get('file_name')
        folder_path = request_json.get('folder_path', 'audio_files/')
        if not all([bucket_name, file_name]):
            return (jsonify({'error': 'bucket_name and file_name are required'}), 400, headers)
        if folder_path and not folder_path.endswith('/'):
            folder_path += '/'
        resource_name = file_name.strip()
        api_url = f"https://generativelanguage.googleapis.com/v1beta/{resource_name}:download?alt=media"
        response = requests.get(api_url, headers={'x-goog-api-key': api_key}, timeout=300)
        if response.status_code not in [200, 400]:
            return (jsonify({'error': f'Failed to download: {response.status_code}', 'details': response.text}), response.status_code, headers)
        if response.status_code == 400:
            try:
                error_json = json.loads(response.text)
                if 'error' in error_json:
                    return (jsonify({'error': f'API error: {response.status_code}', 'details': response.text}), 400, headers)
            except (json.JSONDecodeError, ValueError):
                pass
        jsonl_content = response.text.strip()
        if not jsonl_content:
            return (jsonify({'error': 'Batch result file is empty'}), 400, headers)
        lines = jsonl_content.split('\n')
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        uploaded_files = []
        failed_entries = []
        for i, line in enumerate(lines):
            try:
                data = json.loads(line)
                key = data.get('key', f"audio_{i}")
                resp_body = data.get('response', {})
                candidates = resp_body.get('candidates', [])
                if not candidates:
                    failed_entries.append({'line': i + 1, 'key': key, 'error': 'No candidates'})
                    continue
                parts = candidates[0].get('content', {}).get('parts', [])
                audio_base64 = None
                mime_type = "audio/mp3"
                for part in parts:
                    if 'inlineData' in part:
                        audio_base64 = part['inlineData'].get('data')
                        mime_type = part['inlineData'].get('mimeType', mime_type)
                        break
                if not audio_base64:
                    failed_entries.append({'line': i + 1, 'key': key, 'error': 'No audio data'})
                    continue
                audio_bytes = base64.b64decode(audio_base64)
                if 'L16' in mime_type or 'pcm' in mime_type.lower():
                    audio_bytes = pcm_to_wav(audio_bytes, sample_rate=24000)
                    mime_type = "audio/wav"
                    extension = "wav"
                else:
                    extension = mime_type.split('/')[-1] if '/' in mime_type else 'mp3'
                dest_filename = f"{folder_path}{key}.{extension}"
                blob = bucket.blob(dest_filename)
                blob.upload_from_string(audio_bytes, content_type=mime_type)
                uploaded_files.append({'key': key, 'gcs_uri': f"gs://{bucket_name}/{dest_filename}"})
            except Exception as e:
                failed_entries.append({'line': i + 1, 'error': str(e)})
        return (jsonify({'success': True,'uploaded_count': len(uploaded_files),'failed_count': len(failed_entries),'uploaded_files': uploaded_files,'errors': failed_entries}), 200, headers)
    except Exception as e:
        return (jsonify({'error': 'Internal server error', 'details': str(e)}), 500, headers)
