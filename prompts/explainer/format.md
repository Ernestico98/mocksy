# script.json format

```json
{
  "title": "Designing a URL shortener that survives a viral link",
  "description": "One or two sentences for the video description: what problem is solved and what the viewer learns.",
  "phases": [
    {"key": "scope", "label": "Scope"},
    {"key": "design", "label": "Design"},
    {"key": "deep", "label": "Deep dive"}
  ],
  "scenes": [
    {
      "id": "s01", "phase": "scope", "title": "The problem",
      "beats": [
        {"say": "We're building a URL shortener. Paste a long link, get a seven character code, and anyone who opens it lands on the original page.",
         "show": "A long URL types into a text field; an arrow collapses it into a short chip 'sho.rt/x7Kq2'."},
        {"say": "Say we create ten million links a day, and each link is opened about a hundred times. That's roughly a hundred and twenty writes per second, and twelve thousand reads.",
         "show": "Two counters appear side by side: 'writes ≈ 120 / s' (small, blue) and 'reads ≈ 12,000 / s' (large, green), with a '100 : 1' chip between them."}
      ]
    },
    {
      "id": "s02", "phase": "design", "title": "The read path",
      "beats": [
        {"say": "A redirect is a lookup: a short code comes in, a long URL goes out.",
         "show": "Browser box, arrow to a 'Redirect service' box; a '301 → long URL' chip returns."},
        {"say": "Because reads outnumber writes a hundred to one, a cache sits in front of the database.",
         "show": "A 'Cache' box appears above, a 'links' table below; arrows from the service to both."},
        {"say": "A hit answers in about a millisecond. A miss falls through to the database and fills the cache on the way back.",
         "show": "A green dot travels to the cache with chip 'hit ≈ 1 ms'; an amber dot goes to the table, then a 'fill' arrow goes up to the cache."}
      ]
    }
  ],
  "diagram": {
    "title": "URL shortener: final design",
    "nodes": [
      {"id": "client", "label": "Browser", "col": 0, "row": 1, "color": "muted"},
      {"id": "lb", "label": "Load balancer", "col": 1, "row": 1},
      {"id": "redirect", "label": "Redirect service", "sub": "stateless, autoscaled", "col": 2, "row": 1},
      {"id": "cache", "label": "Cache", "sub": "code → URL, LRU", "col": 3, "row": 0, "color": "teal"},
      {"id": "db", "label": "Links DB", "sub": "sharded by code", "col": 3, "row": 2, "color": "purple"}
    ],
    "edges": [
      {"from": "client", "to": "lb", "label": "GET /x7Kq2"},
      {"from": "lb", "to": "redirect"},
      {"from": "redirect", "to": "cache", "label": "hit"},
      {"from": "redirect", "to": "db", "label": "miss", "dashed": true}
    ]
  },
  "assumptions": ["10 million new links per day", "100 reads per link"]
}
```

## Fields
- `title`: the video title (max 100 characters). `description`: 1-3 sentences (max 700 characters).
- `phases`: 1-5 header chips shown during the video, in order; every scene's `phase` is one of their keys.
- `scenes[].id`: "s01", "s02", ... in order. `title`: max 48 characters.
- `beats[].say`: the narration, spoken exactly as written. `beats[].show`: the visual direction.
- `diagram.nodes[]`: `id`, `label` (max 32 characters), optional `sub` (max 44), grid position `col` 0-5 and
  `row` 0-4 (one node per cell; lay flows out left to right), optional `color`: blue (default), green, amber,
  red, purple (data), teal (caches, queues), muted (clients, external).
- `diagram.edges[]`: `from`, `to`, optional `label` (max 28), `dashed` (async or fallback paths),
  `both` (two-way), `color`.
- `assumptions`: every figure you made up rather than took from the notes, as short phrases.
