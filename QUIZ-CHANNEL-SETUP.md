# Asian Pop Quiz channel setup

The second channel posts the quiz + would-you-rather Shorts. The pipeline and
workflow (`.github/workflows/quiz-channel.yml`) are already in the repo and do
nothing until the three secrets below exist.

## 1. Create the channel (same Gmail is fine)

1. On youtube.com, click your profile picture, then **Settings**, then **Add or manage your channel(s)**, then **Create a channel**.
2. Name it **Asian Pop Quiz**. This makes a "Brand Account" channel under the same login. Switch channels from your profile picture.

## 2. Google Cloud project (recommended)

Upload quota is 10,000 units per day **per Cloud project**. Each upload costs about 1,600 units, so give the quiz channel its own project:

1. Go to https://console.cloud.google.com, then **New project**, and name it `asian-pop-quiz`.
2. Open **APIs & Services** > **Library** > **YouTube Data API v3** and click **Enable**.
3. Open **OAuth consent screen**:
   - Choose **External**.
   - Fill in an app name and your email.
   - Add your Gmail as a test user.
   - Then click **Publish app**, so it is **In production**. If you skip this, the token expires after 7 days.
4. Open **Credentials** > **Create credentials** > **OAuth client ID** > **Desktop app**. Copy the client ID and secret.

## 3. Get the upload token

```
node youtube-automation/setup-youtube.js --out youtube-credentials-quiz.json
```

- Paste the client ID and secret when asked.
- In the browser, pick your Gmail and then **Asian Pop Quiz**. Don't pick Zero Yen Otaku (the old Mr. WorldWideWebster channel).
- The token is saved to `youtube-credentials-quiz.json`. That file is gitignored and is never committed.

## 4. Branding

```
node scripts/setup-quiz-channel.js           # preview
node scripts/setup-quiz-channel.js --apply   # sets description, keywords, banner
```

Then, in YouTube Studio > **Customization** (the API can't do these):

- Set the handle to **@asianpopquiz**.
- Set the profile picture to `branding/quiz-channel/avatar.png`.

## 5. GitHub secrets

Add these to the repo under **Settings** > **Secrets and variables** > **Actions**:

- `QUIZ_YOUTUBE_CLIENT_ID`
- `QUIZ_YOUTUBE_CLIENT_SECRET`
- `QUIZ_YOUTUBE_REFRESH_TOKEN`

Or from this folder with the gh CLI (the values are never printed):

```
node -e "process.stdout.write(require('./youtube-credentials-quiz.json').client_id)" | gh secret set QUIZ_YOUTUBE_CLIENT_ID -R drcompilationyt7-alt/ChinaWeatherReader
node -e "process.stdout.write(require('./youtube-credentials-quiz.json').client_secret)" | gh secret set QUIZ_YOUTUBE_CLIENT_SECRET -R drcompilationyt7-alt/ChinaWeatherReader
node -e "process.stdout.write(require('./youtube-credentials-quiz.json').refresh_token)" | gh secret set QUIZ_YOUTUBE_REFRESH_TOKEN -R drcompilationyt7-alt/ChinaWeatherReader
```

## 6. First run

1. Go to **Actions** > **Quiz Channel** > **Run workflow**.
2. Run it with `dry_run = true` first. Download the `quiz-channel-dry-run` artifact and watch it.
3. After that it runs on its own every day:
   - 05:17 UTC run: two Shorts, scheduled for 12:00 and 16:00 UTC
   - 13:47 UTC run: one Short, scheduled for 20:00 UTC

## What it posts

| Format | Example |
|---|---|
| emoji | Guess the anime / K-pop song from 4 emojis |
| trivia | K-pop, anime, VTuber, C-drama and Asia city questions (A/B/C/D, read out loud) |
| wyr | Would You Rather (anime, K-pop, food, travel), with "but..." twists |
| city | A dot on the map of China / Japan / Korea: which city is it? |
| flag, shape | Asia editions of the flag and country-shape quizzes |

- **Question bank:**
  - It starts from hand-checked items in `core/quiz/assets/pop-bank.json`.
  - Gemini (or OpenRouter) writes new items when a topic runs low.
  - A second fact-check call drops anything it can't confirm.
  - Used questions don't repeat for 60 days.
  - Everything the channel learns lives in `memory/quiz-channel/`.
