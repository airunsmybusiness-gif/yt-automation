"""Sanity check: call Agent 4 (Haiku) on existing sentences. ~$0.02."""
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sanity")

# Verify env
required = ["ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    log.error(f"Missing env vars: {missing}")
    log.error("Run: export $(grep -v '^#' .env | xargs)")
    sys.exit(1)

from supabase import create_client
from execution.agents.agent_runner import (
    run_agent4_optimizer,
    AGENT_MODELS,
    MODEL_HAIKU,
)

VIDEO_ID = "2UWUFccMmvs"

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# Load video record
v = sb.table("yt_viral_videos").select("*").eq("video_id", VIDEO_ID).limit(1).execute()
if not v.data:
    log.error(f"Video {VIDEO_ID} not found")
    sys.exit(1)
video = v.data[0]
log.info(f"Loaded video: {video['title'][:60]}")

# Load existing sentences from yt_scripts
s = sb.table("yt_scripts").select("*").eq("viral_video_id", video["id"]).order("sentence_number").limit(10).execute()
if not s.data:
    log.error("No sentences found in yt_scripts")
    sys.exit(1)

# Use just 10 sentences for the sanity check (cheap)
sentences = [
    {"sentence_number": r["sentence_number"], "sentence_text": r["sentence_text"], "section": r.get("section", "")}
    for r in s.data
]
log.info(f"Loaded {len(sentences)} sentences for cheap test")
log.info(f"Optimizer model: {AGENT_MODELS['optimizer']} (expected: {MODEL_HAIKU})")

if AGENT_MODELS["optimizer"] != MODEL_HAIKU:
    log.error("MODEL MISMATCH — patch did not apply correctly")
    sys.exit(1)

log.info("Calling Agent 4 (Optimizer) with Haiku...")
try:
    result = run_agent4_optimizer(sb, video, sentences)
    log.info(f"SUCCESS — got {len(result) if isinstance(result, list) else 'dict'} back")
    log.info(f"First result item: {json.dumps(result[0] if isinstance(result, list) and result else result, indent=2)[:300]}")
except Exception as e:
    log.error(f"FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

log.info("=" * 60)
log.info("SANITY CHECK PASSED")
log.info("Check console.anthropic.com/cost — Haiku call should be < $0.02")
log.info("=" * 60)
