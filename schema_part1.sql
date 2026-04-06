-- ============================================================
-- SUPABASE SCHEMA — YouTube Automation Pipeline
-- All 6 parts in order. Run each part sequentially in
-- Supabase SQL Editor.
-- Last updated: March 9, 2026
-- ============================================================


-- ============================================================
-- PART 1: Types, Tables, Sequences, Constraints, Views
-- ============================================================

CREATE TYPE public.transcript_provider AS ENUM (
    'supadata',
    'gemini'
);

CREATE TYPE public.transcript_type AS ENUM (
    'source',
    'produced'
);

CREATE TABLE public.yt_agent_prompts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_name text NOT NULL,
    prompt_content text NOT NULL,
    is_active boolean DEFAULT true,
    version integer DEFAULT 1,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE TABLE public.yt_api_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    account_name text NOT NULL,
    api_key text NOT NULL,
    quota_exhausted boolean DEFAULT false,
    last_used timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.yt_audio_files (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    viral_video_id uuid NOT NULL,
    batch_number integer NOT NULL,
    file_url text NOT NULL,
    file_path text NOT NULL,
    start_sentence_number integer NOT NULL,
    end_sentence_number integer NOT NULL,
    chunk_size integer NOT NULL,
    sentence_count integer,
    duration_seconds numeric,
    file_size_bytes bigint,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.yt_batch_jobs (
    id bigint NOT NULL,
    batch_job_name text NOT NULL,
    status text DEFAULT 'pending'::text,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    images_generated integer DEFAULT 0,
    images_failed integer DEFAULT 0,
    viral_video_id uuid,
    media_type text
);

CREATE SEQUENCE public.yt_batch_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.yt_comments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    video_record_id uuid,
    video_id text NOT NULL,
    comment_id text NOT NULL,
    parent_id text,
    author_name text,
    author_channel_id text,
    content text,
    like_count integer DEFAULT 0,
    is_reply boolean DEFAULT false,
    is_own_video boolean DEFAULT false,
    published_at timestamp with time zone,
    updated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.yt_competitors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    channel_username text NOT NULL,
    channel_id text,
    channel_name text,
    subscriber_count integer,
    is_active boolean DEFAULT true,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.yt_viral_videos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    video_id text NOT NULL,
    url text NOT NULL,
    title text,
    channel_title text,
    channel_id text,
    channel_username text,
    published_at timestamp with time zone,
    views integer,
    likes integer,
    comments integer,
    duration text,
    thumbnail text,
    tags text[],
    description text,
    scraped_at timestamp with time zone DEFAULT now(),
    age_hours numeric,
    source_type text,
    source_value text,
    status text DEFAULT 'queued'::text,
    production_started_at timestamp with time zone,
    production_completed_at timestamp with time zone,
    production_notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    transcript_status text DEFAULT 'no_transcript'::text NOT NULL,
    thumbnail_description text,
    comments_status text DEFAULT 'no_comments'::text NOT NULL,
    suitable boolean,
    thread_id text,
    CONSTRAINT check_comments_status CHECK ((comments_status = ANY (ARRAY['no_comments'::text, 'processing'::text, 'completed'::text, 'failed'::text]))),
    CONSTRAINT check_transcript_status CHECK ((transcript_status = ANY (ARRAY['no_transcript'::text, 'processing'::text, 'completed'::text, 'failed'::text])))
);

CREATE VIEW public.yt_competitor_performance AS
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

CREATE VIEW public.yt_daily_stats AS
 SELECT date(yt_viral_videos.scraped_at) AS date,
    count(*) AS videos_found,
    count(DISTINCT yt_viral_videos.channel_username) AS unique_channels,
    avg(yt_viral_videos.views) AS avg_views,
    sum(CASE WHEN (yt_viral_videos.source_type = 'channel'::text) THEN 1 ELSE 0 END) AS from_channels,
    sum(CASE WHEN (yt_viral_videos.source_type = 'search'::text) THEN 1 ELSE 0 END) AS from_search
   FROM public.yt_viral_videos
  GROUP BY (date(yt_viral_videos.scraped_at))
  ORDER BY (date(yt_viral_videos.scraped_at)) DESC;

CREATE TABLE public.yt_image_generation_jobs (
    id bigint NOT NULL,
    sentence_number integer NOT NULL,
    formatted_prompt text NOT NULL,
    reference_image_path text,
    status character varying(20) DEFAULT 'pending'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    batch_job_name text,
    viral_video_id uuid
);

CREATE SEQUENCE public.yt_image_generation_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.yt_search_keywords (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    keyword text NOT NULL,
    category text,
    is_active boolean DEFAULT true,
    priority integer DEFAULT 5,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE VIEW public.yt_keyword_performance AS
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

CREATE TABLE public.yt_results (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    gcs_video_url text,
    video_id text,
    thumbnail_link text
);

ALTER TABLE public.yt_results ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.yt_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.yt_scripts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    viral_video_id uuid NOT NULL,
    sentence_number integer NOT NULL,
    sentence_text text NOT NULL,
    section text,
    original_comparison text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    audio_file_id uuid,
    production_id uuid
);

CREATE TABLE public.yt_strategist_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    video_record_id uuid,
    video_id text,
    strategy_brief jsonb,
    title_options jsonb,
    ranking_justification text,
    thumbnail_concept jsonb,
    video_metadata jsonb,
    script_writer_instructions jsonb,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.yt_supadata_api_keys (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    api_key text,
    quota_exhausted boolean
);

ALTER TABLE public.yt_supadata_api_keys ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.yt_supadata_api_keys_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.yt_video_transcripts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    video_record_id uuid NOT NULL,
    video_id text NOT NULL,
    content text,
    language_code text DEFAULT 'en'::text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    type public.transcript_type DEFAULT 'source'::public.transcript_type NOT NULL,
    provider public.transcript_provider
);

CREATE TABLE public.yt_viral_analyzer_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    video_record_id uuid,
    video_id text,
    analysis_metadata jsonb,
    title_analysis jsonb,
    script_structure jsonb,
    audience_intelligence jsonb,
    visual_psychology jsonb,
    viral_formula_synthesis jsonb,
    human_readable_summary jsonb,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.yt_workflow_settings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    setting_key text NOT NULL,
    setting_value jsonb NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY public.yt_batch_jobs ALTER COLUMN id SET DEFAULT nextval('public.yt_batch_jobs_id_seq'::regclass);
ALTER TABLE ONLY public.yt_image_generation_jobs ALTER COLUMN id SET DEFAULT nextval('public.yt_image_generation_jobs_id_seq'::regclass);

ALTER TABLE ONLY public.yt_video_transcripts
    ADD CONSTRAINT unique_video_transcript_type UNIQUE (video_record_id, type);
ALTER TABLE ONLY public.yt_agent_prompts
    ADD CONSTRAINT yt_agent_prompts_agent_name_key UNIQUE (agent_name);
ALTER TABLE ONLY public.yt_agent_prompts
    ADD CONSTRAINT yt_agent_prompts_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_api_accounts
    ADD CONSTRAINT yt_api_accounts_api_key_key UNIQUE (api_key);
ALTER TABLE ONLY public.yt_api_accounts
    ADD CONSTRAINT yt_api_accounts_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_audio_files
    ADD CONSTRAINT yt_audio_files_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_batch_jobs
    ADD CONSTRAINT yt_batch_jobs_batch_job_name_key UNIQUE (batch_job_name);
ALTER TABLE ONLY public.yt_batch_jobs
    ADD CONSTRAINT yt_batch_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_comments
    ADD CONSTRAINT yt_comments_comment_id_key UNIQUE (comment_id);
ALTER TABLE ONLY public.yt_comments
    ADD CONSTRAINT yt_comments_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_competitors
    ADD CONSTRAINT yt_competitors_channel_id_unique UNIQUE (channel_id);
ALTER TABLE ONLY public.yt_competitors
    ADD CONSTRAINT yt_competitors_channel_username_key UNIQUE (channel_username);
ALTER TABLE ONLY public.yt_competitors
    ADD CONSTRAINT yt_competitors_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_image_generation_jobs
    ADD CONSTRAINT yt_image_generation_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_results
    ADD CONSTRAINT yt_results_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_scripts
    ADD CONSTRAINT yt_scripts_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_search_keywords
    ADD CONSTRAINT yt_search_keywords_keyword_key UNIQUE (keyword);
ALTER TABLE ONLY public.yt_search_keywords
    ADD CONSTRAINT yt_search_keywords_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_strategist_results
    ADD CONSTRAINT yt_strategist_results_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_supadata_api_keys
    ADD CONSTRAINT yt_supadata_api_keys_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_video_transcripts
    ADD CONSTRAINT yt_video_transcripts_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_video_transcripts
    ADD CONSTRAINT yt_video_transcripts_video_record_id_key UNIQUE (video_record_id);
ALTER TABLE ONLY public.yt_viral_analyzer_results
    ADD CONSTRAINT yt_viral_analyzer_results_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_viral_videos
    ADD CONSTRAINT yt_viral_videos_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_viral_videos
    ADD CONSTRAINT yt_viral_videos_url_key UNIQUE (url);
ALTER TABLE ONLY public.yt_viral_videos
    ADD CONSTRAINT yt_viral_videos_video_id_key UNIQUE (video_id);
ALTER TABLE ONLY public.yt_workflow_settings
    ADD CONSTRAINT yt_workflow_settings_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.yt_workflow_settings
    ADD CONSTRAINT yt_workflow_settings_setting_key_key UNIQUE (setting_key);

ALTER TABLE ONLY public.yt_batch_jobs
    ADD CONSTRAINT fk_yt_batch_jobs_viral_video FOREIGN KEY (viral_video_id) REFERENCES public.yt_viral_videos(id) ON DELETE RESTRICT;
ALTER TABLE ONLY public.yt_results
    ADD CONSTRAINT fk_yt_results_youtube_id FOREIGN KEY (video_id) REFERENCES public.yt_viral_videos(video_id) ON DELETE CASCADE;
ALTER TABLE ONLY public.yt_audio_files
    ADD CONSTRAINT yt_audio_files_viral_video_id_fkey FOREIGN KEY (viral_video_id) REFERENCES public.yt_viral_videos(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.yt_comments
    ADD CONSTRAINT yt_comments_video_record_id_fkey FOREIGN KEY (video_record_id) REFERENCES public.yt_viral_videos(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.yt_image_generation_jobs
    ADD CONSTRAINT yt_image_generation_jobs_batch_job_name_fkey FOREIGN KEY (batch_job_name) REFERENCES public.yt_batch_jobs(batch_job_name);
ALTER TABLE ONLY public.yt_image_generation_jobs
    ADD CONSTRAINT yt_image_generation_jobs_viral_video_id_fkey FOREIGN KEY (viral_video_id) REFERENCES public.yt_viral_videos(id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE ONLY public.yt_scripts
    ADD CONSTRAINT yt_scripts_audio_file_id_fkey FOREIGN KEY (audio_file_id) REFERENCES public.yt_audio_files(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.yt_scripts
    ADD CONSTRAINT yt_scripts_viral_video_id_fkey FOREIGN KEY (viral_video_id) REFERENCES public.yt_viral_videos(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.yt_strategist_results
    ADD CONSTRAINT yt_strategist_results_video_record_id_fkey FOREIGN KEY (video_record_id) REFERENCES public.yt_viral_videos(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.yt_video_transcripts
    ADD CONSTRAINT yt_video_transcripts_video_record_id_fkey FOREIGN KEY (video_record_id) REFERENCES public.yt_viral_videos(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.yt_viral_analyzer_results
    ADD CONSTRAINT yt_viral_analyzer_results_video_record_id_fkey FOREIGN KEY (video_record_id) REFERENCES public.yt_viral_videos(id) ON DELETE CASCADE;


-- ============================================================
