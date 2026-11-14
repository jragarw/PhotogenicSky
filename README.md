# Photogenic Sky

Photogenic Sky brings weather, birding, and shared light-quality guidance together for each saved Home Assistant location.

## Features

- **Photogenic Sky score** ranks forecast sunrise, sunset, and midday windows using light, cloud cover, rain probability, and wind.
- **Birding score** combines seasonal regional bird evidence, light quality, and weather suitability. The same soft dawn and dusk light that benefits photography contributes directly to this score; harsh overhead light receives a lower light score. Midday scores make that light tradeoff visible.
- **Regional bird outlook** lists species recorded in the same upcoming calendar period in the previous five years, within a configurable 10, 25, or 50 km radius. Choose a 7- or 14-day period in the integration options.
- **Shoreline tide outlook** can be enabled per location. Where a NOAA prediction station is within 100 km, the integration reports 30 days of high and low tides and shows rising/falling direction, the next tide turn, and nearby events for each forecast sunrise and sunset.
- **14-day weather outlook** includes temperature, precipitation, wind, cloud cover, UV, sunrise, and sunset.

Each configured location creates separate Photogenic Sky, Birding Score, and Regional Bird Outlook sensors. The Tide Outlook sensor appears when shoreline support is enabled. Data for different locations and providers remains independent: a bird or tide API outage does not invalidate a successful weather forecast.

## Data sources and limits

- Weather: [Open-Meteo](https://open-meteo.com/), free for non-commercial use without an API key. Review its terms and attribution requirements.
- Seasonal bird evidence: [GBIF](https://www.gbif.org/developer/occurrence), a free public occurrence API with no key. The regional species list is historical evidence from matching dates in the preceding five years, not a true future migration forecast or a guarantee that any species will be present.
- Tides: [NOAA Tides & Currents](https://tidesandcurrents.noaa.gov/api/), free for supported US stations. If no nearby station is found, or a location is outside NOAA coverage, tide data will be unavailable. UK and other non-US coverage is not provided by NOAA.
- Location lookup: OpenStreetMap Nominatim.

The birding score weights seasonal bird evidence at 55%, light quality at 30%, and weather suitability at 15%. Weather suitability accounts for rain, wind, and cloud cover. Photogenic Sky score combines light quality, cloud cover, and precipitation. These are planning guides, not guarantees. Sensor attributes expose the components and source coverage.

## Installation and setup

Install through HACS as a custom integration using this repository, restart Home Assistant, and add **Photogenic Sky** under **Settings → Devices & services**. Enter a city, postcode, or place name. Open the integration's options to choose the 7- or 14-day bird outlook, regional search radius, and whether the location is near the shoreline.

The integration refreshes its planning data every 12 hours. It requires no paid service or API key.
