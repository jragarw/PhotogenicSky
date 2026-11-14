# Photogenic Sky Planner (Docker)

A self-hosted browser app for planning photography and birding conditions. It presents the weather, light, clouds, bird evidence, astro windows, tide calendar, and the inputs behind each score in one report. It runs without Home Assistant, paid APIs, API keys, or external font/CDN dependencies.

## Run it

From the `docker-app` directory:

```sh
docker compose up --build -d
```

Open `http://<docker-host>:8080`, add a location, and choose a matching place or enter latitude and longitude directly. Location settings are stored in the persistent `photogenic_sky_data` Docker volume and remain when the container is replaced. Back up that volume before removing it; `docker compose down -v` deletes it.

The app refreshes provider data when first opened and caches each location report for four hours. Use the refresh button to request fresh data immediately. Set `CACHE_SECONDS` in `compose.yaml` to change the cache period.

To protect the browser and API with HTTP Basic Auth, set `APP_PASSWORD` (and optionally `APP_USERNAME`) in the service environment before starting the app. Do not expose an unauthenticated instance to the public internet; use a reverse proxy with authentication/TLS if remote access is needed.

## What the report shows

- **Photogenic score:** the best upcoming daylight window in the seven-day forecast. Its explanation shows the light, clear-sky, and dry-weather contributions and their forecast inputs.
- **Birding score:** regional historical bird evidence, shared light quality, and weather suitability. The browser lists the leading species and evidence totals. GBIF records are historical observations for matching calendar dates in the preceding five years, not a live count or forecast.
- **Astrophotography score:** forecast night windows only after the Sun falls below −6° solar elevation (the end of blue hour). The daylight score is zero in those windows; the astro score shows darkness, cloud, and moon-illumination contributions.
- **Light and weather:** current solar phase/elevation, total and low/mid/high cloud cover, sunrise, solar noon, sunset, UV, seven-day weather, and the detailed conditions at each planning window.
- **Tide calendar:** 30 days of local high/low events when shoreline is enabled, plus tide context near today’s sunrise and sunset.

Scores are comparative planning guides, not guarantees. Tide predictions are harmonic estimates and are not for navigation; check the station and datum before relying on tide heights.

## Glance JSON endpoint

Each saved location has a compact summary endpoint:

```text
GET http://<docker-host>:8080/api/glance/<location-id>
```

Find the ID in `GET /api/locations`. The JSON includes the three scores, light phase, cloud cover, best photo/astro opportunities, bird evidence, next tide, a short summary, and update time. Glance can consume this endpoint with a `custom-api` widget. The Glance container must be able to reach the app over the network. Example starting point:

```yaml
- type: custom-api
  title: Photogenic Sky · Holywell
  url: http://photogenic-sky:8080/api/glance/<location-id>
  cache: 15m
  template: |
    <div class="flex flex-column">
      <div class="size-h3">{{ .JSON.String "summary" }}</div>
      <div>Photo {{ .JSON.String "photogenic_score_display" }}</div>
      <div>Birding {{ .JSON.String "birding_score_display" }}</div>
      <div>Astro {{ .JSON.String "astro_score_display" }}</div>
    </div>
```

The endpoint returns JSON; it does not require Glance or Home Assistant. If Basic Auth is enabled, configure Glance to send matching credentials or keep the app and Glance behind the same authenticated reverse proxy.

## Providers and attribution

- Weather, cloud layers, astronomy times, and geocoding: [Open-Meteo](https://open-meteo.com/), free without a key under its terms and attribution requirements.
- Regional seasonal bird evidence: [GBIF](https://www.gbif.org/developer/occurrence), public occurrence records, no key.
- Tide predictions: [Open Waters](https://openwaters.io/) (TICON-4, CC BY 4.0 attribution); NOAA Tides & Currents is a fallback for supported US stations.

Providers refresh independently. If one fails, its status and error are shown in the report and other successful data remains available.

## API

- `GET /api/health`
- `GET /api/locations`
- `POST /api/locations` · create a location
- `PUT /api/locations/{id}` · update a location
- `DELETE /api/locations/{id}`
- `GET /api/report/{id}` · complete report and score explanations (`?refresh=1` bypasses cache)
- `GET /api/glance/{id}` · compact dashboard payload
- `GET /api/geocode?q=<place>` · place search

This is the standalone application path in the Photogenic Sky repository. The existing Home Assistant integration remains separate and is not required to run this app.
