# Photogenic Sky Planner (Docker)

A self-hosted browser app for planning photography and birding conditions. It presents the weather, light, clouds, bird evidence, astro windows, tide calendar, and the inputs behind each score in one report. It runs without Home Assistant, paid APIs, API keys, or external font/CDN dependencies.

## Deploy with Dockge

The published image is `ghcr.io/jragarw/photogenic-sky:latest`. In Dockge, create a stack and paste [`compose.yaml`](compose.yaml). It pulls the image from GHCR and mounts persistent location settings at `/data`.

The Compose file uses the TrueNAS path you provided:

```text
/mnt/FastStorage/docker/data/codeserver/photogenic-sky
```

Create that directory or dataset first and grant write access to UID/GID `568` (TrueNAS `apps`). If your dataset uses another owner, set `PUID` and `PGID` in Dockge’s stack environment to that numeric UID/GID. Choose an unused host port with `WEB_PORT` if port 8080 is occupied. Set `APP_PASSWORD` to enable browser/API Basic Auth; `APP_USERNAME` defaults to `photogenic`.

Open `http://<truenas-host>:8080` and add locations in the browser. When a new image is published, use Dockge’s **Pull** / **Redeploy** action to fetch and start it. The Compose file uses `pull_policy: always`, so a compose up also checks GHCR for the latest image. Do not remove the data directory when updating the image.

The image is built from `docker-app/` and published to GHCR whenever app files are merged to `main`. It is tagged `latest` and with the app version from `VERSION`; bump that file for a versioned update. The first publish is private by default: set the package visibility to **Public** in the repository’s Packages settings so TrueNAS can pull without registry credentials. There is no API key or paid data provider.

For local source development, use `docker compose -f compose.dev.yaml up --build` instead; that file builds from the checkout and stores data in a named volume.

The app refreshes provider data when a report is opened and caches each location for four hours. Use the refresh button to fetch new data immediately. Set `CACHE_SECONDS` in `compose.yaml` to change the cache period.

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
