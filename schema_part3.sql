-- PART 3: Policies and ACLs (RLS)
-- ============================================================

ALTER SEQUENCE public.yt_batch_jobs_id_seq OWNED BY public.yt_batch_jobs.id;
ALTER SEQUENCE public.yt_image_generation_jobs_id_seq OWNED BY public.yt_image_generation_jobs.id;

COMMENT ON COLUMN public.yt_video_transcripts.provider IS
    'The tool used to fetch or generate this text (supadata for scraping, gemini for AI rewriting)';

CREATE INDEX idx_yt_agent_prompts_agent_name ON public.yt_agent_prompts USING btree (agent_name);
CREATE INDEX idx_yt_agent_prompts_is_active ON public.yt_agent_prompts USING btree (is_active);
CREATE INDEX idx_yt_api_accounts_active ON public.yt_api_accounts USING btree (quota_exhausted) WHERE (quota_exhausted = false);
CREATE INDEX idx_yt_api_accounts_quota ON public.yt_api_accounts USING btree (quota_exhausted, last_used);
CREATE INDEX idx_yt_audio_files_batch_number ON public.yt_audio_files USING btree (batch_number);
CREATE INDEX idx_yt_audio_files_sentences ON public.yt_audio_files USING btree (viral_video_id, start_sentence_number, end_sentence_number);
CREATE INDEX idx_yt_audio_files_viral_video_id ON public.yt_audio_files USING btree (viral_video_id);
CREATE INDEX idx_yt_batch_jobs_status ON public.yt_batch_jobs USING btree (status);
CREATE INDEX idx_yt_batch_jobs_viral_video_id ON public.yt_batch_jobs USING btree (viral_video_id);
CREATE INDEX idx_yt_comments_is_own ON public.yt_comments USING btree (is_own_video);
CREATE INDEX idx_yt_comments_video_id ON public.yt_comments USING btree (video_id);
CREATE INDEX idx_yt_image_generation_jobs_batch_job_name ON public.yt_image_generation_jobs USING btree (batch_job_name);
CREATE INDEX idx_yt_image_generation_jobs_sentence_number ON public.yt_image_generation_jobs USING btree (sentence_number);
CREATE INDEX idx_yt_image_generation_jobs_status ON public.yt_image_generation_jobs USING btree (status);
CREATE INDEX idx_yt_keywords_active ON public.yt_search_keywords USING btree (is_active);
CREATE INDEX idx_yt_keywords_keyword ON public.yt_search_keywords USING btree (keyword);
CREATE INDEX idx_yt_keywords_priority ON public.yt_search_keywords USING btree (priority DESC);
CREATE INDEX idx_yt_results_video_id ON public.yt_results USING btree (video_id);
CREATE INDEX idx_yt_scripts_audio_file_id ON public.yt_scripts USING btree (audio_file_id);
CREATE INDEX idx_yt_scripts_section ON public.yt_scripts USING btree (section);
CREATE INDEX idx_yt_scripts_sentence_number ON public.yt_scripts USING btree (sentence_number);
CREATE INDEX idx_yt_scripts_video_sentence ON public.yt_scripts USING btree (viral_video_id, sentence_number);
CREATE INDEX idx_yt_scripts_viral_video_id ON public.yt_scripts USING btree (viral_video_id);
CREATE INDEX idx_yt_transcripts_lookup ON public.yt_video_transcripts USING btree (video_record_id, type);
CREATE INDEX idx_yt_transcripts_video_id ON public.yt_video_transcripts USING btree (video_id);
CREATE INDEX idx_yt_transcripts_video_record_id ON public.yt_video_transcripts USING btree (video_record_id);
CREATE INDEX idx_yt_viral_videos_channel_id ON public.yt_viral_videos USING btree (channel_id);
CREATE INDEX idx_yt_viral_videos_channel_username ON public.yt_viral_videos USING btree (channel_username);
CREATE INDEX idx_yt_viral_videos_comments_status ON public.yt_viral_videos USING btree (comments_status);
CREATE INDEX idx_yt_viral_videos_published_at ON public.yt_viral_videos USING btree (published_at DESC);
CREATE INDEX idx_yt_viral_videos_scraped_at ON public.yt_viral_videos USING btree (scraped_at DESC);
CREATE INDEX idx_yt_viral_videos_source ON public.yt_viral_videos USING btree (source_type, source_value);
CREATE INDEX idx_yt_viral_videos_status ON public.yt_viral_videos USING btree (status);
CREATE INDEX idx_yt_viral_videos_url ON public.yt_viral_videos USING btree (url);
CREATE INDEX idx_yt_viral_videos_video_id ON public.yt_viral_videos USING btree (video_id);
CREATE INDEX idx_yt_viral_videos_views ON public.yt_viral_videos USING btree (views DESC);

CREATE POLICY "Allow authenticated users to delete yt_scripts" ON public.yt_scripts FOR DELETE TO authenticated USING (true);
CREATE POLICY "Allow authenticated users to insert yt_scripts" ON public.yt_scripts FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Allow authenticated users to read yt_scripts" ON public.yt_scripts FOR SELECT TO authenticated USING (true);
CREATE POLICY "Allow authenticated users to update yt_scripts" ON public.yt_scripts FOR UPDATE TO authenticated USING (true);
CREATE POLICY "Authenticated users can insert comments" ON public.yt_comments FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Authenticated users can insert/update yt_agent_prompts" ON public.yt_agent_prompts FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Authenticated users can read comments" ON public.yt_comments FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read keywords" ON public.yt_search_keywords FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read settings" ON public.yt_workflow_settings FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read transcripts" ON public.yt_video_transcripts FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read viral videos" ON public.yt_viral_videos FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read yt_agent_prompts" ON public.yt_agent_prompts FOR SELECT TO authenticated USING ((is_active = true));
CREATE POLICY "Authenticated users can update quota status" ON public.yt_api_accounts FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users can update video status" ON public.yt_viral_videos FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users can update yt_agent_prompts" ON public.yt_agent_prompts FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Authenticated users can view API accounts" ON public.yt_api_accounts FOR SELECT TO authenticated USING (true);
CREATE POLICY "Service role full access" ON public.yt_competitors TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON public.yt_video_transcripts TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON public.yt_viral_videos TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access to comments" ON public.yt_comments TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access to competitors" ON public.yt_competitors TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access to keywords" ON public.yt_search_keywords TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access to settings" ON public.yt_workflow_settings TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access to viral videos" ON public.yt_viral_videos TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role has full access" ON public.yt_api_accounts TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role has full access to yt_agent_prompts" ON public.yt_agent_prompts TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.yt_agent_prompts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_competitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_scripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_search_keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_supadata_api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_video_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_viral_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_workflow_settings ENABLE ROW LEVEL SECURITY;

GRANT ALL ON TABLE public.yt_agent_prompts TO service_role;
GRANT SELECT,INSERT,UPDATE ON TABLE public.yt_agent_prompts TO authenticated;
GRANT ALL ON TABLE public.yt_api_accounts TO service_role;
GRANT ALL ON TABLE public.yt_audio_files TO postgres;
GRANT ALL ON TABLE public.yt_audio_files TO service_role;
GRANT ALL ON TABLE public.yt_audio_files TO authenticated;
GRANT ALL ON TABLE public.yt_batch_jobs TO postgres;
GRANT ALL ON TABLE public.yt_batch_jobs TO service_role;
GRANT ALL ON TABLE public.yt_batch_jobs TO authenticated;
GRANT SELECT,USAGE ON SEQUENCE public.yt_batch_jobs_id_seq TO service_role;
GRANT SELECT,USAGE ON SEQUENCE public.yt_batch_jobs_id_seq TO authenticated;
GRANT ALL ON TABLE public.yt_comments TO postgres;
GRANT ALL ON TABLE public.yt_comments TO service_role;
GRANT ALL ON TABLE public.yt_comments TO authenticated;
GRANT ALL ON TABLE public.yt_comments TO anon;
GRANT ALL ON TABLE public.yt_competitors TO service_role;
GRANT SELECT ON TABLE public.yt_competitors TO authenticated;
GRANT ALL ON TABLE public.yt_viral_videos TO service_role;
GRANT SELECT ON TABLE public.yt_viral_videos TO authenticated;
GRANT ALL ON TABLE public.yt_competitor_performance TO service_role;
GRANT SELECT ON TABLE public.yt_competitor_performance TO authenticated;
GRANT ALL ON TABLE public.yt_daily_stats TO service_role;
GRANT SELECT ON TABLE public.yt_daily_stats TO authenticated;
GRANT ALL ON TABLE public.yt_image_generation_jobs TO postgres;
GRANT ALL ON TABLE public.yt_image_generation_jobs TO service_role;
GRANT ALL ON TABLE public.yt_image_generation_jobs TO authenticated;
GRANT SELECT,USAGE ON SEQUENCE public.yt_image_generation_jobs_id_seq TO authenticated;
GRANT SELECT,USAGE ON SEQUENCE public.yt_image_generation_jobs_id_seq TO service_role;
GRANT ALL ON TABLE public.yt_search_keywords TO service_role;
GRANT SELECT ON TABLE public.yt_search_keywords TO authenticated;
GRANT ALL ON TABLE public.yt_keyword_performance TO service_role;
GRANT SELECT ON TABLE public.yt_keyword_performance TO authenticated;
GRANT ALL ON TABLE public.yt_results TO postgres;
GRANT ALL ON TABLE public.yt_results TO service_role;
GRANT ALL ON TABLE public.yt_results TO authenticated;
GRANT ALL ON TABLE public.yt_scripts TO postgres;
GRANT ALL ON TABLE public.yt_scripts TO service_role;
GRANT ALL ON TABLE public.yt_scripts TO authenticated;
GRANT ALL ON TABLE public.yt_strategist_results TO postgres;
GRANT ALL ON TABLE public.yt_strategist_results TO service_role;
GRANT ALL ON TABLE public.yt_strategist_results TO authenticated;
GRANT ALL ON TABLE public.yt_supadata_api_keys TO authenticated;
GRANT ALL ON TABLE public.yt_supadata_api_keys TO service_role;
GRANT ALL ON SEQUENCE public.yt_supadata_api_keys_id_seq TO authenticated;
GRANT ALL ON SEQUENCE public.yt_supadata_api_keys_id_seq TO service_role;
GRANT ALL ON TABLE public.yt_video_transcripts TO authenticated;
GRANT ALL ON TABLE public.yt_video_transcripts TO service_role;
GRANT ALL ON TABLE public.yt_viral_analyzer_results TO postgres;
GRANT ALL ON TABLE public.yt_viral_analyzer_results TO service_role;
GRANT ALL ON TABLE public.yt_viral_analyzer_results TO authenticated;
GRANT ALL ON TABLE public.yt_workflow_settings TO service_role;
GRANT SELECT ON TABLE public.yt_workflow_settings TO authenticated;


-- ============================================================
-- PART 4: Seed Data — yt_workflow_settings
-- ============================================================

INSERT INTO public.yt_workflow_settings (id, setting_key, setting_value, description, created_at, updated_at)
VALUES (
    '7ab67fca-ca44-44ff-ab9a-df185b0d96dd',
    'viral_threshold',
    '{"minViews": 7000, "earlyHours": 12, "earlyViews": 4000, "maxAgeHours": 48}',
    'Thresholds for determining if a video is viral',
    '2025-12-22 15:16:56.353375+00',
    '2025-12-25 15:49:28.922074+00'
)
ON CONFLICT (id) DO UPDATE
    SET setting_key = EXCLUDED.setting_key,
        setting_value = EXCLUDED.setting_value,
        description = EXCLUDED.description,
        updated_at = EXCLUDED.updated_at;


-- ============================================================
-- PART 5: Seed Data — yt_agent_prompts
-- (All 6 agent prompts: analyzer, strategist, script_writer,
--  optimizer, image_generator, thumbnail_style)
-- NOTE: Full prompt content is stored in Supabase directly.
-- This part seeds the agent name + active status only.
-- Load full prompt_content from the Skool community SQL export.
-- ============================================================

-- Agent names registered in this system:
-- agent1_analyzer
-- agent2_strategist
-- agent3_script_writer
-- agent4_optimizer
-- image_generator
-- thumbnail_style

-- To re-seed full prompts, run the complete Part 5 SQL from
-- the Supabase Setup lesson in the Skool community.
-- The prompts are too large to include here but are already
-- in your Supabase instance if you ran the original setup.


-- ============================================================
-- PART 6: Security — Enable RLS on remaining tables
-- ============================================================

ALTER TABLE public.yt_api_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_audio_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_batch_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_image_generation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_strategist_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yt_viral_analyzer_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Authenticated users full access yt_audio_files"
    ON public.yt_audio_files TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY "Authenticated users full access yt_batch_jobs"
    ON public.yt_batch_jobs TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY "Authenticated users full access yt_image_generation_jobs"
    ON public.yt_image_generation_jobs TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY "Authenticated users full access yt_strategist_results"
    ON public.yt_strategist_results TO authenticated USING (true) WITH CHECK (true);

CREATE POLICY "Authenticated users full access yt_viral_analyzer_results"
    ON public.yt_viral_analyzer_results TO authenticated USING (true) WITH CHECK (true);

-- Recreate views with security_invoker to respect RLS
CREATE OR REPLACE VIEW public.yt_competitor_performance WITH (security_invoker=on) AS
 SELECT c.channel_username,
    c.channel_name,
    c.subscriber_count,
    count(v.id) AS total_viral_videos,
    avg(v.views) AS avg_views,
    max(v.views) AS max_views
   FROM (public.yt_competitors c
     LEFT JOIN public.yt_viral_videos v ON ((c.channel_username = v.channel_username)))
  WHERE (c.is_active = true)
  GROUP BY c.channel_username, c.channel_name, c.subscriber_count;

CREATE OR REPLACE VIEW public.yt_daily_stats WITH (security_invoker=on) AS
 SELECT date(yt_viral_videos.scraped_at) AS date,
    count(*) AS videos_found,
    count(DISTINCT yt_viral_videos.channel_username) AS unique_channels,
    avg(yt_viral_videos.views) AS avg_views,
    sum(CASE WHEN (yt_viral_videos.source_type = 'channel'::text) THEN 1 ELSE 0 END) AS from_channels,
    sum(CASE WHEN (yt_viral_videos.source_type = 'search'::text) THEN 1 ELSE 0 END) AS from_search
   FROM public.yt_viral_videos
  GROUP BY (date(yt_viral_videos.scraped_at))
  ORDER BY (date(yt_viral_videos.scraped_at)) DESC;

CREATE OR REPLACE VIEW public.yt_keyword_performance WITH (security_invoker=on) AS
 SELECT k.keyword,
    k.category,
    k.priority,
    count(v.id) AS total_videos_found,
    avg(v.views) AS avg_views,
    max(v.views) AS max_views,
    count(CASE WHEN (v.status = 'queued'::text) THEN 1 ELSE NULL::integer END) AS queued_count
   FROM (public.yt_search_keywords k
     LEFT JOIN public.yt_viral_videos v ON (((k.keyword = v.source_value) AND (v.source_type = 'search'::text))))
  WHERE (k.is_active = true)
  GROUP BY k.keyword, k.category, k.priority
  ORDER BY (count(v.id)) DESC;


-- ============================================================
-- END OF SCHEMA
-- ============================================================
