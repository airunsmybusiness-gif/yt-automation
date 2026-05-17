# Video Quality Directive — MindSeam Pipeline v2

## Objective
Produce 8-10 minute psychology/self-improvement videos that look professionally edited,
retain attention, and are indistinguishable from manually-produced faceless channels
like Psych2Go, TopThink, and BetterThanYesterday.

## What was wrong with LOzrFoSHnGA
1. **40-second static slides** — audio chunks grouped 8 sentences, 1 image per chunk
2. **Ken Burns shake** — zoompan at 0.0003 looked like camera tremor, not cinematic
3. **21 minutes** — no length cap, script ran away
4. **Gemini TTS** — robotic, uncanny valley
5. **No hook** — script started slow, no pattern interrupts
6. **No thumbnail** — uploaded without one
7. **No SEO** — title/desc/tags were manual afterthoughts

## The fix: timing math

### Target: 8-10 minutes (480-600 seconds)
- Script: 80-120 sentences
- Average sentence: 5-7 seconds spoken (Edge TTS natural pacing)
- 1 image per 2 consecutive sentences → 40-60 images
- Each image holds ~10-14 seconds → fast enough to not bore, slow enough to read

### Image pairing
```
Sentences 1-2   → Image 1    (hold ~12s)
Sentences 3-4   → Image 2    (hold ~11s)
Sentences 5-6   → Image 3    (hold ~13s)
...
Sentence 119-120 → Image 60  (hold ~10s)
```

If sentence count is odd, last image gets 3 sentences.

### Transitions: crossfade, not Ken Burns
```
FFmpeg xfade filter between consecutive segments:
  -filter_complex "[0][1]xfade=transition=fade:duration=0.3:offset={hold_time-0.3}"

This gives a smooth 0.3s dissolve between slides.
No zoompan. No panning. No shake.
```

### Audio: one file per sentence
```
Edge TTS generates one MP3 per sentence.
Sentences paired to same image get concatenated:
  ffmpeg -i sent_1.mp3 -i sent_2.mp3 -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" pair_1.mp3
```

### Render pipeline (per video)
```
For each image pair (img_N, audio_pair_N):
  1. Create segment: image loop + paired audio → segment_N.ts
  2. Scale to 1280x720, pad if needed
  3. libx264 -preset medium -crf 23, AAC 192k

Then crossfade-concat all segments:
  Use complex filtergraph with xfade chain

Add no background music (psychology channels don't use it — cleaner, more serious).

Output: /tmp/{video_id}/final.mp4
```

## Attention retention rules (enforced in agent prompts)

### First 30 seconds (5-beat hook)
1. **Pattern interrupt** — surprising fact or question (sentence 1)
2. **Promise** — what viewer will learn (sentence 2)
3. **Proof** — credential or social proof (sentence 3)
4. **Preview** — quick list of what's coming (sentence 4-5)
5. **Push** — "stay until the end for #7, it changed everything" (sentence 5-6)

### Every 60-90 seconds
- Open loop: "but here's what nobody tells you..."
- Restate promise: "this next one is why most relationships fail"
- Pattern interrupt: rhetorical question, counterintuitive fact

### Last 30 seconds
- Strong CTA: subscribe, like, share
- Tease next video topic
- End on emotional note (not generic "thanks for watching")

## Thumbnail specification

### Design rules
- 3-5 words MAX text overlay
- Font: bold sans-serif, white with black outline/shadow
- Color: high contrast, warm tones (psychology = orange/red/purple)
- Composition: left-third text, right-third focal image
- Must be readable at 120×68 px (mobile thumbnail size)

### Generation
- Replicate Flux Dev, same model as video images
- Prompt: Strategist thumbnail_brief + "YouTube thumbnail, bold text overlay, high contrast, 16:9"
- Size: 1280×720
- Text overlay added via PIL (not in the AI image — AI text is unreliable)

## SEO specification

### Title
- 50-70 characters
- Number + emotion + curiosity gap
- Examples: "7 Psychological Tricks Your Brain Plays on You"
- NO clickbait that doesn't deliver

### Description
- First 2 lines: hook + keyword-rich summary (shows in search)
- Timestamps for each section
- Related video topics for algorithm signals
- Subscribe CTA
- 3-5 relevant hashtags at bottom

### Tags
- 15-20 tags
- Mix: 5 high-volume ("psychology", "self improvement")
- 10 medium ("psychological facts", "brain tricks")
- 5 long-tail ("psychology facts about human behavior 2026")

### Category
- 27 (Education) — best for monetization eligibility
- NOT 22 (People & Blogs) — lower CPM

## Quality gates (pipeline must check)

1. Script length: 80-120 sentences or reject
2. Image generation: all images must be > 10KB or retry
3. Audio: each sentence audio must be 1-15 seconds or flag
4. Final video: must be 7-11 minutes or flag
5. Upload: thumbnail must be set or retry
6. Cost: if Replicate spend > $2 for this video, halt and alert
