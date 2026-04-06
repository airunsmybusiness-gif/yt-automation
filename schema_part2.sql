-- PART 2: Functions and Triggers
-- ============================================================

CREATE OR REPLACE FUNCTION public.fn_sync_comments_status() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  UPDATE public.yt_viral_videos
  SET
    comments_status = 'completed',
    updated_at = now()
  WHERE id = NEW.video_record_id
    AND comments_status != 'completed';
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.sync_transcript_status() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF (TG_OP = 'INSERT') THEN
    UPDATE public.yt_viral_videos
    SET transcript_status = 'completed'
    WHERE id = NEW.video_record_id;
  ELSIF (TG_OP = 'DELETE') THEN
    IF NOT EXISTS (SELECT 1 FROM public.yt_video_transcripts WHERE video_record_id = OLD.video_record_id) THEN
      UPDATE public.yt_viral_videos
      SET transcript_status = 'no_transcript'
      WHERE id = OLD.video_record_id;
    END IF;
  END IF;
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


CREATE OR REPLACE FUNCTION public.update_yt_agent_prompts_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$;











CREATE OR REPLACE TRIGGER tr_sync_transcript_status
    AFTER INSERT OR DELETE ON public.yt_video_transcripts
    FOR EACH ROW EXECUTE FUNCTION public.sync_transcript_status();

CREATE OR REPLACE TRIGGER tr_update_video_comments_status
    AFTER INSERT ON public.yt_comments
    FOR EACH ROW EXECUTE FUNCTION public.fn_sync_comments_status();

CREATE OR REPLACE TRIGGER update_yt_agent_prompts_timestamp
    BEFORE UPDATE ON public.yt_agent_prompts
    FOR EACH ROW EXECUTE FUNCTION public.update_yt_agent_prompts_updated_at();

CREATE OR REPLACE TRIGGER update_yt_competitors_updated_at
    BEFORE UPDATE ON public.yt_competitors
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE OR REPLACE TRIGGER update_yt_scripts_updated_at
    BEFORE UPDATE ON public.yt_scripts
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE OR REPLACE TRIGGER update_yt_search_keywords_updated_at
    BEFORE UPDATE ON public.yt_search_keywords
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE OR REPLACE TRIGGER update_yt_video_transcripts_updated_at
    BEFORE UPDATE ON public.yt_video_transcripts
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE OR REPLACE TRIGGER update_yt_viral_videos_updated_at
    BEFORE UPDATE ON public.yt_viral_videos
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE OR REPLACE TRIGGER update_yt_workflow_settings_updated_at
    BEFORE UPDATE ON public.yt_workflow_settings
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


-- ============================================================
