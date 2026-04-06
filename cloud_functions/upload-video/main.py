import os, json, logging, io
from google.cloud import storage, secretmanager
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import functions_framework

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_project_id():
    return os.environ.get('GCP_PROJECT') or os.environ.get('GOOGLE_CLOUD_PROJECT', '')

def get_secret(secret_id):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{get_project_id()}/secrets/{secret_id}/versions/latest"
    return client.access_secret_version(request={"name": name}).payload.data.decode('UTF-8')

def get_youtube_credentials():
    creds = Credentials(token=None, refresh_token=get_secret('youtube-refresh-token'), token_uri='https://oauth2.googleapis.com/token', client_id=get_secret('youtube-client-id'), client_secret=get_secret('youtube-client-secret'), scopes=['https://www.googleapis.com/auth/youtube.upload'])
    creds.refresh(Request())
    return creds

def download_from_gcs(bucket_name, blob_name):
    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    stream = io.BytesIO()
    blob.download_to_file(stream)
    stream.seek(0)
    return stream

@functions_framework.http
def upload_video(request):
    if request.method == 'OPTIONS':
        return ('', 204, {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type'})
    headers = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}
    try:
        rj = request.get_json(silent=True)
        if not rj or not all(k in rj for k in ['bucket_name','file_name','title']):
            return (json.dumps({'error':'Missing fields'}), 400, headers)
        creds = get_youtube_credentials()
        video_stream = download_from_gcs(rj['bucket_name'], rj['file_name'])
        yt = build('youtube', 'v3', credentials=creds)
        body = {'snippet': {'title': rj['title'], 'description': rj.get('description',''), 'tags': rj.get('tags',[]), 'categoryId': rj.get('category_id','22')}, 'status': {'privacyStatus': rj.get('privacy_status','private')}}
        media = MediaIoBaseUpload(video_stream, mimetype='video/*', chunksize=1024*1024, resumable=True)
        req = yt.videos().insert(part='snippet,status', body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        vid = resp['id']
        thumb_ok = False
        if rj.get('thumbnail_file'):
            try:
                ts = download_from_gcs(rj['bucket_name'], rj['thumbnail_file'])
                ts.seek(0)
                m = MediaIoBaseUpload(ts, mimetype='image/jpeg', resumable=True)
                yt.thumbnails().set(videoId=vid, media_body=m).execute()
                thumb_ok = True
            except Exception as e:
                logger.warning(f"Thumbnail failed: {e}")
        return (json.dumps({'success':True,'video_id':vid,'video_url':f'https://www.youtube.com/watch?v={vid}','thumbnail_uploaded':thumb_ok}), 200, headers)
    except Exception as e:
        return (json.dumps({'success':False,'error':str(e)}), 500, headers)
