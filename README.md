# bonuses-feed

Scrapes the "Bonuses" / "Major Milestone Bonuses" sections from [leekduck.com](https://leekduck.com)
event pages (not covered by [ScrapedDuck](https://github.com/bigfoott/ScrapedDuck)'s JSON feeds)
and publishes the result as `bonuses.json`, updated every 2 hours via GitHub Actions.

Consume it with CORS enabled from:

```
https://raw.githubusercontent.com/<user>/bonuses-feed/main/bonuses.json
```

## Schema

Flat array of bonus objects:

```json
{
  "text": "6× XP fürs Fangen von Pokémon",
  "image": "https://cdn.leekduck.com/assets/img/events/bonuses/xp.png",
  "eventName": "Ultra Unlock: 10th Anniversary Edition",
  "eventSlug": "ultra-unlock-10th-anniversary-edition",
  "start": "2026-07-21T10:00:00",
  "end": "2026-07-23T10:00:00",
  "rotating": true
}
```

Only events that are currently running or start within ~14 days are scraped.

## Local run

```bash
pip install -r requirements.txt
python scrape.py
```
