const $ = (id) => document.getElementById(id);
const state = { locations: [], activeId: "", report: null, selectedPlace: null, editing: false };

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}
function safe(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
function score(value) { return value == null || value === "" || !Number.isFinite(Number(value)) ? "—" : Math.round(Number(value)); }
function fmtTime(value, timezone) {
  if (!value) return "—";
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(value)) return value.match(/T(\d{2}:\d{2})/)?.[1] || value;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(-5);
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", timeZone: timezone }).format(d);
}
function fmtDate(value, timezone) {
  return new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${value}T12:00:00Z`));
}
function moonName(phase) {
  if (phase == null) return "Unavailable";
  if (phase < .04 || phase > .96) return "New moon";
  if (phase < .23) return "Waxing crescent";
  if (phase < .27) return "First quarter";
  if (phase < .48) return "Waxing gibbous";
  if (phase < .52) return "Full moon";
  if (phase < .73) return "Waning gibbous";
  if (phase < .77) return "Last quarter";
  return "Waning crescent";
}
function setVisible(id, visible) { $(id).classList.toggle("hidden", !visible); }
function currentLocation() { return state.locations.find(x => x.id === state.activeId); }
const preferenceFields = ["photo_light","photo_sky","photo_weather","bird_evidence","bird_light","bird_weather","astro_darkness","astro_sky","astro_moon"];

async function loadLocations(preferredId) {
  state.locations = await api("/api/locations");
  const select = $("locationSelect");
  select.innerHTML = state.locations.length
    ? state.locations.map(x => `<option value="${safe(x.id)}">${safe(x.name)}${x.place_name ? ` · ${safe(x.place_name)}` : ""}</option>`).join("")
    : `<option value="">Add a location to begin</option>`;
  state.activeId = preferredId || state.activeId || state.locations[0]?.id || "";
  if (!state.locations.some(x => x.id === state.activeId)) state.activeId = state.locations[0]?.id || "";
  select.value = state.activeId;
  setVisible("emptyState", !state.locations.length);
  setVisible("report", false);
  if (state.activeId) await loadReport();
}
async function loadReport(refresh = false) {
  if (!state.activeId) return;
  setVisible("loadingState", true); setVisible("errorBanner", false);
  setVisible("report", false);
  try {
    state.report = await api(`/api/report/${encodeURIComponent(state.activeId)}${refresh ? "?refresh=1" : ""}`);
    renderReport(state.report); setVisible("report", true);
  } catch (err) {
    $("errorBanner").textContent = `Could not load this report: ${err.message}`;
    setVisible("errorBanner", true);
  } finally { setVisible("loadingState", false); }
}
function providerStatus(report) {
  const labels = { weather: "Weather", birds: "Bird records", tides: "Tides" };
  return Object.entries(report.providers || {}).map(([key, item]) => {
    const good = item.status === "available" || item.status === "seasonal_records" || item.status === "no_records" || item.status === "disabled";
    const title = item.error ? ` title="${safe(item.error)}"` : "";
    return `<span class="provider-pill ${good ? "" : "bad"}"${title}>${labels[key] || safe(key)} · <strong>${safe(item.status.replaceAll("_", " "))}</strong></span>`;
  }).join("");
}
function bestText(item, type) {
  if (!item) return "No forecast opportunity available";
  return `${item.window[0].toUpperCase()}${item.window.slice(1)} · ${fmtDate(item.date, state.report.timezone)} · ${fmtTime(item.time, state.report.timezone)}`;
}
function contributions(container, details, names) {
  if (!details?.contributions) { container.innerHTML = ""; return; }
  container.innerHTML = Object.entries(details.contributions).map(([key, value]) => `<span class="contribution">${safe(names[key] || key.replaceAll("_", " "))} +${safe(value)}</span>`).join("");
}
function renderScore(prefix, best, domain, labels) {
  const details = best?.[domain];
  $(`${prefix}Score`).textContent = details ? score(details.score) : "—";
  $(`${prefix}When`).textContent = bestText(best, domain);
  $(`${prefix}Why`).textContent = best?.summary || (domain === "birding" ? (state.report.bird_outlook?.note || "Seasonal bird evidence is not available for this region.") : "No matching forecast opportunity was available.");
  contributions($(`${prefix}Contributions`), details, labels);
}
function renderReport(report) {
  const loc = report.location; const tz = report.timezone || "UTC"; const light = report.lighting || {};
  $("placeName").textContent = loc.place_name || `${loc.latitude.toFixed(3)}, ${loc.longitude.toFixed(3)}`;
  $("reportTitle").textContent = loc.name;
  $("phasePill").textContent = light.phase || "weather unavailable";
  $("refreshStamp").textContent = report.updated_at ? `Updated ${new Date(report.updated_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}` : "Not updated";
  $("providerStatus").innerHTML = providerStatus(report);
  const bestPhoto = report.best_photo; const bestBird = report.best_birding; const bestAstro = report.best_astro;
  renderScore("photo", bestPhoto, "photogenic", {light:"light",clear_sky:"clear sky",dry_weather:"low rain"});
  renderScore("bird", bestBird, "birding", {seasonal_species_evidence:"bird evidence",light_quality:"light",weather_suitability:"weather"});
  renderScore("astro", bestAstro, "astro", {darkness:"darkness",clear_sky:"clear sky",low_moon_illumination:"low moonlight"});
  if (Number(light.solar_elevation_degrees) < -6) {
    const upcomingScore = bestPhoto?.photogenic?.score;
    $("photoKicker").textContent = "CURRENT PHOTOGENIC SCORE";
    $("photoScore").textContent = "0";
    $("photoWhen").textContent = bestPhoto ? `Inactive after blue hour · next daylight ${bestText(bestPhoto, "photogenic")}` : "Inactive after blue hour · no daylight window in forecast";
    $("photoWhy").textContent = `There is no sunlight now, so the photogenic score is 0 and astro is the active score. The next daylight window is forecast at ${upcomingScore ?? "—"}/100; the input contributions below describe that upcoming window.`;
    contributions($("photoContributions"), bestPhoto?.photogenic, {light:"next light",clear_sky:"next clear sky",dry_weather:"next low rain"});
  } else {
    $("photoKicker").textContent = "BEST UPCOMING DAYLIGHT WINDOW";
  }
  $("sunElevation").textContent = light.solar_elevation_degrees == null ? "—" : Number(light.solar_elevation_degrees).toFixed(1);
  $("sunPhaseText").textContent = light.phase ? `${light.phase} · blue hour ends at −6°` : "Sun position unavailable";
  $("currentTime").textContent = light.observed_at ? `Local time ${fmtTime(light.observed_at, tz)}` : "";
  const needle = Math.max(0, Math.min(100, ((Number(light.solar_elevation_degrees || -18) + 18) / 36) * 100));
  $("sunNeedle").style.left = `${needle}%`;
  const clouds = [
    ["Total cloud", light.cloud_cover_percent], ["Low · blocking", light.low_cloud_percent],
    ["Mid · textured", light.mid_cloud_percent], ["High · wispy", light.high_cloud_percent],
  ];
  $("cloudRows").innerHTML = clouds.map(([name, val]) => `<div class="cloud-row"><span>${name}</span><div class="cloud-track"><div class="cloud-fill" style="width:${Math.max(0,Math.min(100,Number(val)||0))}%"></div></div><b class="cloud-value">${val == null ? "—" : `${Math.round(val)}%`}</b></div>`).join("");
  const todayParts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year:"numeric", month:"2-digit", day:"2-digit" }).formatToParts(new Date()).reduce((o,p)=>(o[p.type]=p.value,o),{});
  const todayText = `${todayParts.year}-${todayParts.month}-${todayParts.day}`;
  const today = report.weather_days?.find(d => d.date === todayText) || report.weather_days?.[0];
  if (today) {
    $("sunriseTime").textContent = fmtTime(today.sunrise, tz); $("sunsetTime").textContent = fmtTime(today.sunset, tz);
    const riseParts = today.sunrise?.match(/T(\d{2}):(\d{2})/), setParts = today.sunset?.match(/T(\d{2}):(\d{2})/);
    if (riseParts && setParts) {
      const noonMinutes = Math.round(((Number(riseParts[1])*60+Number(riseParts[2])) + (Number(setParts[1])*60+Number(setParts[2]))) / 2);
      $("noonTime").textContent = `${String(Math.floor(noonMinutes/60)%24).padStart(2,"0")}:${String(noonMinutes%60).padStart(2,"0")}`;
    } else $("noonTime").textContent = "—";
    $("uvMax").textContent = today.uv_index_max == null ? "—" : Number(today.uv_index_max).toFixed(1);
    $("moonPhaseNow").textContent = moonName(today.moon_phase);
    $("moonriseTime").textContent = fmtTime(today.moonrise, tz);
    $("moonsetTime").textContent = fmtTime(today.moonset, tz);
    const ctx = report.tide_context_today || {};
    $("sunriseTide").textContent = ctx.sunrise ? `${ctx.sunrise.type} tide ${fmtTime(ctx.sunrise.time,tz)}` : "";
    $("sunsetTide").textContent = ctx.sunset ? `${ctx.sunset.type} tide ${fmtTime(ctx.sunset.time,tz)}` : "";
  }
  renderForecast(report); renderBirds(report); renderTides(report);
  $("dataUpdated").textContent = `Weather source: ${report.providers.weather.source} · Refreshed ${new Date(report.updated_at).toLocaleString()}`;
}
function scoreTone(score) { return score >= 75 ? "var(--green)" : score >= 50 ? "var(--amber)" : "#dca08d"; }
function opportunityText(item, key, timezone) {
  if (!item) return "—";
  return `${item.window} ${fmtTime(item.time,timezone)} (${item[key]?.score ?? "—"}/100)`;
}
function renderForecast(report) {
  $("forecastTable").innerHTML = (report.daily_summary || []).map((day, index) => {
    const opportunities = (report.opportunities || []).filter(x => x.date === day.date);
    const opportunitiesHtml = opportunities.map(o => {
      const scoreValue = o.astro?.score ?? o.photogenic.score;
      const scoreLabel = o.astro ? `Astro ${scoreValue}/100` : o.photogenic.score === 0 && o.photogenic.night_gate ? "Photo 0 · astro only" : `Photo ${scoreValue}/100`;
      const cloud = o.cloud_cover_percent == null ? "cloud —" : `cloud ${Math.round(o.cloud_cover_percent)}%`;
      const rain = o.rain_probability_percent == null ? "rain —" : `rain ${Math.round(o.rain_probability_percent)}%`;
      const wind = o.wind_speed_kmh == null ? "wind —" : `wind ${Math.round(o.wind_speed_kmh)} km/h`;
      const moon = o.astro?.moon_illumination_percent == null ? "moon —" : `moon ${o.astro.moon_illumination_percent}%`;
      const lightScore = o.light?.score;
      const explain = o.astro ? o.summary : `${o.summary} Inputs: ${cloud}, ${rain}, ${wind}. Solar elevation ${o.solar_elevation_degrees}°.`;
      return `<article class="window-card"><div class="window-top"><b>${safe(o.window.toUpperCase())} · ${fmtTime(o.time,report.timezone)}</b><span class="window-score" style="color:${scoreTone(scoreValue)}">${safe(scoreLabel)}</span></div><p>${safe(explain)}</p><div class="window-inputs"><span>light ${safe(lightScore ?? "—")}</span><span>${safe(cloud)}</span><span>${safe(rain)}</span><span>${safe(wind)}</span>${o.astro?`<span>${safe(moon)}</span>`:""}${o.birding?.score!=null?`<span>birds ${safe(o.birding.score)}</span>`:""}</div></article>`;
    }).join("");
    const highLow = day.temperature_max_c == null ? "—" : `${Math.round(day.temperature_max_c)}° / ${Math.round(day.temperature_min_c)}°`;
    const top = [day.photo && `Photo ${day.photo.photogenic.score}`, day.birding && `Birding ${day.birding.birding.score}`, day.astro && `Astro ${day.astro.astro.score}`].filter(Boolean).join(" · ") || "No score";
    const tides = day.tides?.length ? day.tides.map(t=>`${safe(t.type)} ${fmtTime(t.time,report.timezone)}${t.height_m==null?"":` · ${Number(t.height_m).toFixed(1)} m`}`).join(" · ") : (currentLocation()?.shoreline ? "No tide events" : "Tides disabled");
    const birdEvidence = day.bird_evidence?.status === "seasonal_records" ? `${day.bird_evidence.species_count} regional species · historical evidence` : "Seasonal bird evidence unavailable";
    const keyTimes = `Sunrise ${fmtTime(day.sunrise,report.timezone)} · Noon ${fmtTime(day.solar_noon,report.timezone)} · Sunset ${fmtTime(day.sunset,report.timezone)} · Moon ${fmtTime(day.moonrise,report.timezone)} / ${fmtTime(day.moonset,report.timezone)}`;
    return `<details class="forecast-day" ${index===0?"open":""}><summary><span class="day-name">${fmtDate(day.date,report.timezone)}</span><span class="day-condition">${safe(day.condition)} · ${safe(highLow)}</span><span class="day-summary"><span>Rain <b>${day.precipitation_probability_max ?? "—"}%</b></span><span>Wind <b>${day.wind_speed_kmh ?? "—"}</b></span></span><span class="day-best">${safe(top)}</span><span class="chevron">⌄</span></summary><div class="daily-lineup"><div><b>Best photo</b><span>${safe(opportunityText(day.photo,"photogenic",report.timezone))}</span></div><div><b>Birding</b><span>${safe(opportunityText(day.birding,"birding",report.timezone))} · ${safe(birdEvidence)}</span></div><div><b>Astro</b><span>${safe(opportunityText(day.astro,"astro",report.timezone))}</span></div><div><b>Key times</b><span>${safe(keyTimes)}</span></div><div><b>Tides · local day</b><span>${tides}</span></div></div><div class="window-grid">${opportunitiesHtml || `<div class="no-data">No photo or night windows returned for this date.</div>`}</div></details>`;
  }).join("");
}
function renderBirds(report) {
  const birds = report.bird_outlook || {}; const available = birds.status === "seasonal_records" || birds.status === "no_records";
  $("birdWindowNote").textContent = `${birds.window_days || currentLocation()?.lookahead_days || 7} days · ${birds.radius_km || currentLocation()?.radius_km || 25} km radius`;
  $("birdEvidenceSummary").innerHTML = available ? `<div class="evidence-stat"><b>${birds.species_count ?? 0}</b><span>regional species</span></div><div class="evidence-stat"><b>${birds.recurring_species_count ?? 0}</b><span>seen in 3+ years</span></div><div class="evidence-stat"><b>${birds.total_historical_records ?? 0}</b><span>historical records</span></div>` : `<div class="no-data">Bird evidence unavailable: ${safe(birds.error || "provider did not return data")}. Scores still expose their light and weather inputs; birding score remains unavailable without regional evidence.</div>`;
  $("birdSpecies").innerHTML = (birds.species || []).map((s,i)=>`<div class="species-row"><span class="species-icon">${i+1<10?`0${i+1}`:i+1}</span><div><div class="species-name"><a href="https://www.gbif.org/species/${encodeURIComponent(s.taxon_key)}" target="_blank" rel="noopener noreferrer">${safe(s.common_name || s.scientific_name || `GBIF taxon ${s.taxon_key}`)}</a></div><div class="species-sci">${safe(s.scientific_name || "")}</div></div><span class="species-evidence">${safe(s.years_reported)} years · ${safe(s.records)} records</span></div>`).join("") || (available ? `<div class="no-data">No matching seasonal records were returned for this location and period.</div>` : "");
  $("birdNote").textContent = birds.note || "GBIF observations are historical evidence for these calendar dates, not a prediction that a species will be present.";
}
function renderTides(report) {
  const tides = report.tides || {}; const active = currentLocation()?.shoreline;
  $("tideStation").textContent = tides.station ? `${tides.station}${tides.distance_km != null ? ` · ${Number(tides.distance_km).toFixed(1)} km` : ""}` : "";
  if (!active) {
    $("tideMessage").textContent = "Enable shoreline tides in location settings to show predicted high and low tides.";
    $("tideCalendar").innerHTML = ""; $("tideAttribution").textContent = ""; return;
  }
  if (tides.status !== "available") {
    $("tideMessage").innerHTML = `<span class="warning">Tide data unavailable.</span> ${safe(tides.error || tides.detail || "No prediction events returned.")}`;
    $("tideCalendar").innerHTML = ""; $("tideAttribution").textContent = ""; return;
  }
  $("tideMessage").textContent = `${tides.station || tides.source} · ${tides.datum || "local datum"} · 30-day prediction`;
  $("tideCalendar").innerHTML = (report.tide_calendar || []).map(day=>`<div class="tide-day"><div class="tide-date">${fmtDate(day.date,report.timezone)}</div><div class="tide-events">${day.events.map(event=>`<span class="tide-event ${safe(event.type)}">${safe(event.type)} ${fmtTime(event.time,report.timezone)}${event.height_m==null?"":` · ${Number(event.height_m).toFixed(1)} m`}</span>`).join("")}</div></div>`).join("") || `<div class="no-data">No tide events in the requested window.</div>`;
  $("tideAttribution").textContent = `${tides.attribution || tides.source}${tides.note ? ` · ${tides.note}` : ""}`;
}
async function openForm(editing) {
  if (editing && state.activeId) {
    try {
      const saved = await api("/api/locations");
      state.locations = saved;
      $("locationSelect").innerHTML = state.locations.map(x => `<option value="${safe(x.id)}">${safe(x.name)}${x.place_name ? ` · ${safe(x.place_name)}` : ""}</option>`).join("");
      $("locationSelect").value = state.activeId;
    } catch (err) {
      $("formError").textContent = `Could not load saved location settings: ${err.message}`;
      return;
    }
  }
  state.editing = editing;
  const form = $("locationForm"); form.reset(); state.selectedPlace = null;
  $("placeResults").innerHTML = `<option value="">Choose a search result</option>`;
  $("formError").textContent = "";
  const loc = editing ? currentLocation() : null;
  $("formTitle").textContent = loc ? `Edit ${loc.name}` : "Add a location";
  $("deleteLocationButton").classList.toggle("hidden", !loc);
  form.elements.name.value = loc?.name || "";
  form.elements.latitude.value = loc?.latitude ?? "";
  form.elements.longitude.value = loc?.longitude ?? "";
  form.elements.place_name.value = loc?.place_name || "";
  form.elements.lookahead_days.value = String(loc?.lookahead_days || 7);
  form.elements.radius_km.value = String(loc?.radius_km || 25);
  form.elements.shoreline.checked = Boolean(loc?.shoreline);
  const prefs = loc?.preferences || {photo_light:45,photo_sky:30,photo_weather:25,bird_evidence:50,bird_light:25,bird_weather:25,astro_darkness:55,astro_sky:35,astro_moon:10,sky_style:"dramatic"};
  preferenceFields.forEach(key => { form.elements[key].value = prefs[key]; form.querySelector(`[data-weight="${key}"]`).value = `${prefs[key]}%`; }); form.elements.sky_style.value = prefs.sky_style || "dramatic";
  $("locationFormWrap").classList.remove("hidden");
  $("locationFormWrap").scrollIntoView({behavior:"smooth",block:"center"});
}
async function searchPlaces() {
  const query = $("placeQuery").value.trim(); if (!query) return;
  $("formError").textContent = "Searching…";
  try {
    const data = await api(`/api/geocode?q=${encodeURIComponent(query)}`);
    const select = $("placeResults");
    select.innerHTML = `<option value="">Choose a search result</option>` + data.results.map((r,i)=>`<option value="${i}">${safe([r.name,r.admin1,r.country].filter(Boolean).join(", "))}</option>`).join("");
    state.geocodeResults = data.results;
    $("formError").textContent = data.results.length ? "Select the matching place, then save." : "No matching places found. Enter coordinates directly.";
  } catch(err) { $("formError").textContent = err.message; }
}
$("placeResults").addEventListener("change", event=>{
  const place = state.geocodeResults?.[Number(event.target.value)];
  if (!place) return;
  state.selectedPlace = place;
  $("locationForm").elements.latitude.value = place.latitude;
  $("locationForm").elements.longitude.value = place.longitude;
  $("locationForm").elements.place_name.value = [place.name,place.admin1,place.country].filter(Boolean).join(", ");
  if (!$("locationForm").elements.name.value) $("locationForm").elements.name.value = place.name;
  $("formError").textContent = `Coordinates selected · ${Number(place.latitude).toFixed(3)}, ${Number(place.longitude).toFixed(3)}`;
});
preferenceFields.forEach(key => $("locationForm").elements[key].addEventListener("input", event => {
  $("locationForm").querySelector(`[data-weight="${key}"]`).value = `${event.target.value}%`;
}));
$("searchPlaceButton").addEventListener("click", searchPlaces);
$("placeQuery").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();searchPlaces();}});
$("locationForm").addEventListener("submit", async event=>{
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  const payload = {name:data.get("name"),latitude:data.get("latitude"),longitude:data.get("longitude"),place_name:data.get("place_name"),lookahead_days:Number(data.get("lookahead_days")),radius_km:Number(data.get("radius_km")),shoreline:form.elements.shoreline.checked,preferences:{...Object.fromEntries(preferenceFields.map(key=>[key,Number(data.get(key))])),sky_style:data.get("sky_style")}};
  try {
    const loc = state.editing && state.activeId
      ? await api(`/api/locations/${encodeURIComponent(state.activeId)}`,{method:"PUT",body:JSON.stringify(payload)})
      : await api("/api/locations",{method:"POST",body:JSON.stringify(payload)});
    $("locationFormWrap").classList.add("hidden"); state.activeId = loc.id; await loadLocations(loc.id);
  } catch(err) { $("formError").textContent = err.message; }
});
$("locationSelect").addEventListener("change",async event=>{state.activeId=event.target.value;await loadReport();});
$("newLocationButton").addEventListener("click",()=>openForm(false));
$("emptyAddButton").addEventListener("click",()=>openForm(false));
$("editLocationButton").addEventListener("click",()=>openForm(true));
$("closeFormButton").addEventListener("click",()=>$("locationFormWrap").classList.add("hidden"));
$("refreshButton").addEventListener("click",()=>loadReport(true));
$("deleteLocationButton").addEventListener("click",async()=>{
  const loc=currentLocation(); if(!loc || !window.confirm(`Remove ${loc.name}?`)) return;
  try { await api(`/api/locations/${encodeURIComponent(loc.id)}`,{method:"DELETE"}); state.activeId=""; $("locationFormWrap").classList.add("hidden"); await loadLocations(); }
  catch(err) { $("formError").textContent=err.message; }
});
api("/api/locations").then(loadLocations).catch(err=>{$("errorBanner").textContent=err.message;setVisible("errorBanner",true);});
