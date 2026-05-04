# Debugging rules — locked in 2026-05-03

These rules came out of a 5-day debugging marathon that ended in success.
Apply them every session.

## 1. NO GUESSING
Read the actual error message. Read the actual log line. Read the actual row
in the database. Don't theorize about what *might* be happening. Verify.

## 2. One change per deploy
If two things change at once and the deploy fails, the failure is
undiagnosable. Patch one thing, push, verify, then patch the next.

## 3. After every code push, ALWAYS reset the test row
The pipeline auto-marks failed rows as status='failed'. New code does NOT
retry old failures unless the row is reset to status='queued'. Forgetting
this wastes 10+ minutes per cycle.

## 4. SELECT before UPDATE
Supabase shows "Success. No rows returned" for both 0-row and successful
UPDATEs. Always run a SELECT immediately after to confirm the actual state.

## 5. Don't run SQL in zsh
SELECT/FROM/WHERE go in the Supabase SQL Editor web UI, never the terminal.

## 6. Logs lie by truncation
`railway logs --tail 30` only shows scheduler heartbeat. Use `--tail 200 | tail -100`
to see actual pipeline activity. The pipeline runs in worker threads whose
log lines are buried beneath APScheduler's noise.

## 7. status='processing' is a stuck-orphan signal
If a row is processing for >5 min with no log activity, the container crashed
mid-run. Reset to queued; don't wait.

## 8. Filename contracts matter
imagen_images.py writes `0042.jpg` (sentence_number padded). video_render.py
reads `0042.jpg`. They must match exactly. If files exist but render says
"no images found," check filename pattern + extension first.

## 9. OAuth scopes must match exactly
Refresh token scopes minted at auth time must match scopes requested at
refresh time. Mismatch = invalid_scope. Drop the unused scope from code or
re-mint with the broader scope.

## 10. Channel selection in OAuth flow
Google accounts with multiple YouTube channels show a "Choose a channel"
page during OAuth. Always select the intended channel. The refresh token
is bound to whichever channel was selected.

## 11. Refresh tokens are bound to the OAuth client that minted them
Cannot mint a token from one client and use it with another. They're paired.

## 12. Desktop OAuth clients can't use OAuth Playground
Playground only works for Web clients. For Desktop clients, use
google_auth_oauthlib.flow.InstalledAppFlow locally
(see scripts/get_youtube_refresh_token.py).

## 13. Railway "successful" deploy != working code
Healthcheck timeout (default 10s) is too short for our startup. Set to 300s
in Railway Settings → Deploy.

## 14. Setting a Railway env var triggers automatic redeploy
Wait 3 min after setting any variable before assuming code is live.

## 15. Edge TTS is free and works. Don't switch back to paid TTS.
This was the right call from the n8n era. Keep it.
