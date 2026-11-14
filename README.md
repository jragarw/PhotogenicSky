# Photogenic Sky

## Standalone Docker app

The browser-first Photogenic Sky Planner runs without Home Assistant and displays full reports with score inputs, cloud composition, forecast opportunities, bird evidence, astro windows, and a 30-day tide calendar. It also exposes a compact JSON endpoint for Glance.

Deploy the published GHCR image with [Dockge Compose](docker-app/compose.yaml), or use the [Docker app guide](docker-app/README.md) for storage permissions, updates, configuration, API, and provider details. The existing Home Assistant integration remains available as a separate option.


Photogenic Sky brings weather, birding, and shared light-quality guidance together for each saved Home Assistant location.

## Features

- **Photogenic Sky score** ranks forecast sunrise, sunset, and midday windows using light, cloud cover, rain probability, and wind.
- **Birding score** combines seasonal regional bird evidence, light quality, and weather suitability. The same soft dawn and dusk light that benefits photography contributes directly to this score; harsh overhead light receives a lower light score. Midday scores make that light tradeoff visible.
- **Regional bird outlook** lists species recorded in the same upcoming calendar period in the previous five years, within a configurable 10, 25, or 50 km radius. Choose a 7- or 14-day period in the integration options.
- **Shoreline tide outlook** can be enabled per location. The integration reports 30 days of high and low tides from Open Waters, with NOAA fallback and shows rising/falling direction, the next tide turn, and nearby events for each forecast sunrise and sunset.
- **14-day weather outlook** includes temperature, precipitation, wind, cloud cover, UV, sunrise, and sunset.

Each configured location creates separate Photogenic Sky, Birding Score, Photogenic Decision, Birding Decision, Regional Bird Outlook, Current Lighting and Cloud Layers, and Astrophotography Score sensors. The Tide Outlook sensor appears when shoreline support is enabled. Data for different locations and providers remains independent: a bird or tide API outage does not invalidate a successful weather forecast. The Regional Bird Outlook state is the full regional species count; its attributes list the leading 12 species.

Open either score sensor's **More info** panel to inspect its `score_breakdown`, selected forecast window, raw local weather inputs, coordinates, and `decision` text. The two Decision sensors expose the same information in a readable entity state and include formula contributions in their attributes. The birding details also report total species found, recurring species, historical record count, years with records, lookahead period, and search radius. This makes it possible to inspect why locations differ—or verify when rounded scores happen to match—and build dashboard cards from the Decision sensors and their attributes.

## Data sources and limits

- Weather: [Open-Meteo](https://open-meteo.com/), free for non-commercial use without an API key. Review its terms and attribution requirements.
- Seasonal bird evidence: [GBIF](https://www.gbif.org/developer/occurrence), a free public occurrence API with no key. The regional species list is historical evidence from matching dates in the preceding five years, not a true future migration forecast or a guarantee that any species will be present.
- Tides: [Open Waters](https://openwaters.io/), free harmonic tide predictions with CC BY 4.0 attribution, and [NOAA Tides & Currents](https://tidesandcurrents.noaa.gov/api/) as a US fallback. Predictions are approximate; check the station datum and suitability for local use.
- Location lookup: OpenStreetMap Nominatim.

The birding score weights seasonal bird evidence at 55%, light quality at 30%, and weather suitability at 15%. Seasonal evidence is 70% log-scaled regional species richness and 30% coverage across five historical years; the full species count is used, not just the 12 species displayed by name. The count query requests up to 500 species facets and marks when that limit may have been reached. Weather suitability weights low rain 45%, low wind 30%, and low cloud 25%. Photogenic Sky score weights light quality 55%, clear sky 30%, and low rain probability 15%. Light quality starts higher at sunrise and sunset than midday, then adjusts for cloud layers, rain probability, and wind. These are planning guides, not guarantees.

## Installation and setup

Install through HACS as a custom integration using this repository, restart Home Assistant, and add **Photogenic Sky** under **Settings → Devices & services**. Enter a city, postcode, or place name. Open the integration's options to choose the 7- or 14-day bird outlook, regional search radius, and whether the location is near the shoreline.

The integration refreshes its planning data every 12 hours. It requires no paid service or API key.
