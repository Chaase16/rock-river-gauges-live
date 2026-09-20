import hmac
import os
import json
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

BUILD = "5.4"
CENTRAL = ZoneInfo("America/Chicago")

st.set_page_config(
    page_title="Rock River Live",
    page_icon="🦆",
    layout="wide",
)

# Refresh browser every 5 minutes. Individual USGS stations may publish less often.
st_autorefresh(interval=5 * 60 * 1000, key="rock-river-refresh")

# Small mobile/UI cleanup.
st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    div[data-testid="stMetricValue"] {font-size: 2.15rem;}
    div[data-testid="stMetricDelta"] {font-size: 0.95rem;}
    @media (max-width: 700px) {
        .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}
        div[data-testid="stMetricValue"] {font-size: 1.65rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Shared-password login
# ---------------------------------------------------------------------------

def check_password():
    if st.session_state.get("authenticated", False):
        return True

    st.title("🦆 Rock River Live")
    st.caption("Private Rock River & tributary gauge dashboard")

    password = st.text_input("Password", type="password")
    if st.button("Log in", use_container_width=True):
        try:
            expected = st.secrets.get("APP_PASSWORD", "")
        except Exception:
            expected = ""
        if not expected:
            expected = os.getenv("APP_PASSWORD", "")
        if expected and hmac.compare_digest(password, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not check_password():
    st.stop()

# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------

UPPER_BRANCHES = {
    "05423100": "West Branch Rock / CTH D",
    "05423500": "South Branch Rock / Waupun",
    "05424000": "East Branch Rock / Mayville (historical)",
}

MAINSTEM = {
    "05424057": "Horicon",
    "05424081": "Hustisford / Tweedy St",
    "05424157": "Lebanon / County MM",
    "05425500": "Watertown",
    "05426031": "Jefferson",
    "05427085": "Fort Atkinson",
    "05427235": "Lake Koshkonong",
}

TRIBUTARIES = {
    "05425215": "Oconomowoc River / CTH BB",
    "05425912": "Beaverdam River / Beaver Dam",
    "05426000": "Crawfish River / Milford",
    "05426250": "Bark River / Rome",
    "05429700": "Yahara River / Stoughton",
    "05431486": "Turtle Creek / Clinton",
}

NOTES = {
    "05423100": "upper Rock headwater branch",
    "05423500": "upper Rock headwater branch",
    "05424000": "USGS discontinued 10/01/2011 • Horicon is the nearest live downstream proxy",
    "05424081": "main stem below Lake Sinissippi",
    "05425215": "joins Rock upstream of Watertown",
    "05425912": "feeds the Crawfish system",
    "05426000": "joins Rock upstream of Jefferson",
    "05426250": "major Middle Rock tributary",
    "05429700": "joins Rock below Lake Koshkonong",
    "05431486": "joins lower Rock near Beloit",
}

STATIONS = {**UPPER_BRANCHES, **MAINSTEM, **TRIBUTARIES}

# Keep retired stations visible in the dashboard for watershed context, but
# do not query them as though they are live gauges.
HISTORICAL_ONLY_SITES = {
    "05424000": {
        "status": "HISTORICAL",
        "observation": "USGS gauge discontinued 10/01/2011",
        "note": "No active public USGS replacement found • Horicon is the nearest live downstream proxy",
    }
}

PARAM_STAGE = "00065"
PARAM_FLOW = "00060"
LEBANON_SITE = "05424157"

LIVE_MINUTES = 90
RECENT_MINUTES = 24 * 60
HISTORICAL_MINUTES = 7 * 24 * 60

API_BASES = [
    "https://api.waterdata.usgs.gov/ogcapi/v0",
]

# ---------------------------------------------------------------------------
# USGS API helpers
# ---------------------------------------------------------------------------

def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def central_time(dt):
    # Pandas converts missing datetimes to NaT when building the dataframe.
    # Treat both None and NaT as missing.
    if dt is None or pd.isna(dt):
        return None

    # Pandas Timestamp supports tz conversion, but normalize to a native
    # datetime so formatting behaves consistently on every host.
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()

    # API timestamps are timezone-aware. This guard also keeps the function
    # safe if an unexpected naive datetime reaches it.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(CENTRAL)


def format_observation(dt):
    converted = central_time(dt)
    if converted is None:
        return "—"
    return converted.strftime("%m/%d/%Y %I:%M %p %Z")


def pretty_age(minutes):
    if minutes is None or pd.isna(minutes):
        return "—"
    minutes = max(0, int(round(minutes)))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m:02d}m"
    days, rem = divmod(minutes, 24 * 60)
    hours = rem // 60
    return f"{days}d {hours}h"


def read_http_error(exc):
    try:
        return exc.read().decode("utf-8", errors="replace")[:800]
    except Exception:
        return str(exc)


def request_items(base, collection, site_ids, *, period=None):
    params = {
        "f": "json",
        "lang": "en-US",
        "monitoring_location_id": ",".join(f"USGS-{s}" for s in site_ids),
        "parameter_code": f"{PARAM_FLOW},{PARAM_STAGE}",
        "skipGeometry": "TRUE",
        "limit": "50000",
    }
    if period is not None:
        params["time"] = period

    url = f"{base}/collections/{collection}/items?" + urlencode(params, safe=",")

    req = Request(
        url,
        headers={
            "User-Agent": f"RockRiverLiveDashboard/{BUILD}",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    last_error = None
    for attempt in range(1, 4):
        try:
            with urlopen(req, timeout=35) as response:
                return json.load(response), url
        except HTTPError as exc:
            body = read_http_error(exc)
            last_error = RuntimeError(f"HTTP {exc.code} for {collection}: {body}")
            if exc.code == 400:
                raise last_error
            if attempt < 3:
                time.sleep(attempt * 2)
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)

    raise RuntimeError(str(last_error))


def fetch_chunk(collection, site_ids, *, period=None):
    errors = []

    for base in API_BASES:
        try:
            payload, used_url = request_items(
                base, collection, site_ids, period=period
            )
            return payload, [used_url], []
        except Exception as exc:
            errors.append(f"{base}: {exc}")

    if len(site_ids) > 1:
        mid = len(site_ids) // 2

        left_payload, left_urls, left_failed = fetch_chunk(
            collection, site_ids[:mid], period=period
        )
        right_payload, right_urls, right_failed = fetch_chunk(
            collection, site_ids[mid:], period=period
        )

        return (
            {
                "type": "FeatureCollection",
                "features": (
                    left_payload.get("features", [])
                    + right_payload.get("features", [])
                ),
            },
            left_urls + right_urls,
            left_failed + right_failed,
        )

    # One problematic/retired station should not take down the site.
    return (
        {"type": "FeatureCollection", "features": []},
        [],
        [(site_ids[0], " | ".join(errors))],
    )


@st.cache_data(ttl=240, show_spinner=False)
def fetch_collection(collection, period=None):
    ids = [
        site for site in STATIONS.keys()
        if site not in HISTORICAL_ONLY_SITES
    ]
    chunk_size = 6
    features, urls, failures = [], [], []

    for i in range(0, len(ids), chunk_size):
        payload, used_urls, failed = fetch_chunk(
            collection, ids[i:i + chunk_size], period=period
        )
        features.extend(payload.get("features", []))
        urls.extend(used_urls)
        failures.extend(failed)

    return {"type": "FeatureCollection", "features": features}, urls, failures


def props_from_features(payload):
    rows = []

    for feature in payload.get("features", []):
        p = feature.get("properties", {}) or {}

        site_id = str(p.get("monitoring_location_id", ""))
        site = site_id[5:] if site_id.startswith("USGS-") else site_id
        if site not in STATIONS:
            continue

        pcode = str(p.get("parameter_code", ""))
        if pcode not in (PARAM_STAGE, PARAM_FLOW):
            continue

        try:
            value = float(p.get("value"))
        except (TypeError, ValueError):
            continue

        dt = parse_time(p.get("time"))
        if dt is None:
            continue

        rows.append(
            {
                "site": site,
                "pcode": pcode,
                "value": value,
                "time": dt,
                "time_series_id": (
                    p.get("time_series_id") or p.get("timeseries_id") or ""
                ),
            }
        )

    return rows


def choose_latest(rows):
    selected = {}
    for row in rows:
        key = (row["site"], row["pcode"])
        if key not in selected or row["time"] > selected[key]["time"]:
            selected[key] = row
    return selected


def choose_history_series(rows):
    by_series = defaultdict(list)

    for row in rows:
        key = (row["site"], row["pcode"], row["time_series_id"])
        by_series[key].append((row["time"], row["value"]))

    candidates = defaultdict(list)

    for (site, pcode, tsid), points in by_series.items():
        dedup = sorted({t: v for t, v in points}.items())
        if dedup:
            candidates[(site, pcode)].append(
                (len(dedup), dedup[-1][0], tsid, dedup)
            )

    selected = {}
    for key, options in candidates.items():
        # Prefer the series with the newest observation. Point count is only
        # a tie-breaker. This avoids choosing an older/retired series simply
        # because it has more observations.
        options.sort(key=lambda x: (x[1], x[0]), reverse=True)
        selected[key] = options[0][3]

    return selected


def nearest_before(points, target):
    choices = [p for p in points if p[0] <= target]
    return choices[-1] if choices else None


def stage_change(points, hours):
    if not points:
        return None

    newest_time, newest_value = points[-1]
    old = nearest_before(points, newest_time - timedelta(hours=hours))
    if old is None:
        return None

    return newest_value - old[1]


def age_minutes(dt):
    if dt is None or pd.isna(dt):
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    ).total_seconds() / 60


def fmt_delta(value):
    if value is None or pd.isna(value):
        return "—"
    if value > 0.01:
        return f"↑ {value:+.2f}"
    if value < -0.01:
        return f"↓ {value:+.2f}"
    return f"→ {value:+.2f}"


def metric_delta(value):
    if value is None or pd.isna(value):
        return None
    # st.metric adds its own direction arrow, so do not include one here.
    return f"{value:+.2f} ft / 1h"


def status_from_age(minutes):
    if minutes is None:
        return "NO DATA"
    if minutes <= LIVE_MINUTES:
        return "LIVE"
    if minutes <= RECENT_MINUTES:
        return "DELAYED"
    if minutes <= HISTORICAL_MINUTES:
        return "OLD"
    return "HISTORICAL"


def station_link(site):
    return (
        f"https://waterdata.usgs.gov/monitoring-location/USGS-{site}/"
        "#dataTypeId=continuous-00065-0&period=P7D"
    )


NOAA_LEBANON_URL = "https://water.noaa.gov/gauges/lebw3#hg=official"
THERESA_MARSH_DNR_URL = "https://dnr.wisconsin.gov/topic/Lands/WildlifeAreas/theresa"
EAST_BRANCH_RETIRED_USGS_URL = "https://waterdata.usgs.gov/nwis/uv?legacy=1&site_no=05424000"

# NOAA/NWS flood-impact levels currently published for LEBW3.
# NWS separately identifies bankfull at 9.0 ft and flood stage at 10.0 ft.
LEBANON_IMPACTS = [
    (8.0, "Water impacts parts of Monroe Rd"),
    (9.0, "Water over Monroe Rd"),
    (10.0, "Water over the north end of Monroe Rd and Upham Rd"),
    (11.0, "East end of Davidson Rd, Monroe Rd and Upham Rd are closed"),
    (11.5, "Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd, Upham Rd, and Monroe Rd are closed"),
    (13.0, "Wiley Rd, N Crawfish Rd, Red Wing Rd, Morningside Rd, Poplar Grover Rd, Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd and Monroe Rd are closed"),
    (14.0, "Water impacts many properties around town; numerous listed roads are closed"),
]
LEBANON_BANKFULL = 9.0
LEBANON_FLOOD_STAGE = 10.0


def next_lebanon_impact(stage_value):
    if stage_value is None or pd.isna(stage_value):
        return None

    for level, description in LEBANON_IMPACTS:
        if stage_value < level:
            return {
                "level": level,
                "description": description,
                "difference": level - stage_value,
            }

    return {
        "level": None,
        "description": "At or above the highest listed NOAA/NWS impact level",
        "difference": None,
    }


def impact_rows_for_display():
    return pd.DataFrame(
        [
            {
                "Stage ft": level,
                "Flood impact": description,
                "Official marker": (
                    "Bankfull"
                    if level == LEBANON_BANKFULL
                    else "Flood stage"
                    if level == LEBANON_FLOOD_STAGE
                    else ""
                ),
            }
            for level, description in LEBANON_IMPACTS
        ]
    )


def build_lebanon_hydrograph(points, latest_stage_record, full_scale=False):
    if not points:
        return None

    chart_df = pd.DataFrame(points, columns=["Time", "Stage ft"])
    chart_df["Time"] = chart_df["Time"].map(central_time)
    chart_df = chart_df.dropna(subset=["Time", "Stage ft"]).sort_values("Time")

    if chart_df.empty:
        return None

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=chart_df["Time"],
            y=chart_df["Stage ft"],
            mode="lines",
            name="Lebanon stage",
            hovertemplate="%{x|%m/%d %I:%M %p}<br>%{y:.2f} ft<extra></extra>",
        )
    )

    if latest_stage_record is not None:
        marker_time = central_time(latest_stage_record["time"])
        marker_value = latest_stage_record["value"]
        if marker_time is not None:
            fig.add_trace(
                go.Scatter(
                    x=[marker_time],
                    y=[marker_value],
                    mode="markers",
                    name="Current",
                    marker={"size": 11},
                    hovertemplate="Current<br>%{x|%m/%d %I:%M %p}<br>%{y:.2f} ft<extra></extra>",
                )
            )

    # Show the official threshold/impact lines without using a second data API.
    for level, description in LEBANON_IMPACTS:
        label = f"{level:g} ft"
        if level == LEBANON_BANKFULL:
            label += " • bankfull"
        if level == LEBANON_FLOOD_STAGE:
            label += " • flood stage"

        fig.add_hline(
            y=level,
            line_dash="dot",
            annotation_text=label,
            annotation_position="top left",
        )

    data_min = float(chart_df["Stage ft"].min())
    data_max = float(chart_df["Stage ft"].max())

    if full_scale:
        y_low = min(data_min - 0.25, 7.5)
        y_high = max(14.5, data_max + 0.25)
    else:
        current_value = (
            latest_stage_record["value"]
            if latest_stage_record is not None
            else data_max
        )
        next_impact = next_lebanon_impact(current_value)
        if next_impact and next_impact["level"] is not None:
            next_level = next_impact["level"]
            higher_levels = [lvl for lvl, _ in LEBANON_IMPACTS if lvl > next_level]
            second_level = higher_levels[0] if higher_levels else next_level
            y_high = max(data_max + 0.35, second_level + 0.20)
        else:
            y_high = max(data_max + 0.35, 14.5)

        y_low = max(0.0, data_min - 0.35)

    fig.update_layout(
        title="Rock River at Lebanon — 7-day hydrograph",
        xaxis_title=None,
        yaxis_title="Gage height (ft)",
        height=500,
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 60, "b": 20},
        legend={"orientation": "h"},
    )
    fig.update_yaxes(range=[y_low, y_high], ticksuffix=" ft")
    fig.update_xaxes(
        rangeslider={"visible": False},
        rangeselector={
            "buttons": [
                {"count": 1, "label": "24h", "step": "day", "stepmode": "backward"},
                {"count": 2, "label": "48h", "step": "day", "stepmode": "backward"},
                {"count": 7, "label": "7d", "step": "day", "stepmode": "backward"},
                {"step": "all", "label": "All"},
            ]
        },
    )

    return fig


@st.cache_data(ttl=240, show_spinner=False)
def fetch_lebanon_7day_history():
    # Dedicated request keeps the all-gauge table lightweight while giving the
    # Lebanon hydrograph a full seven days of data.
    payload, used_urls, failed = fetch_chunk(
        "continuous", [LEBANON_SITE], period="P7D"
    )
    return payload, used_urls, failed


# ---------------------------------------------------------------------------
# Fetch current + history
# ---------------------------------------------------------------------------

latest_payload, latest_urls, latest_failures = fetch_collection("latest-continuous")
history_payload, history_urls, history_failures = fetch_collection(
    "continuous", period="P2D"
)
lebanon_7d_payload, lebanon_7d_urls, lebanon_7d_failures = fetch_lebanon_7day_history()

latest_by_key = choose_latest(props_from_features(latest_payload))
history_by_key = choose_history_series(props_from_features(history_payload))
lebanon_7d_by_key = choose_history_series(props_from_features(lebanon_7d_payload))


def make_row(site, name, group):
    if site in HISTORICAL_ONLY_SITES:
        meta = HISTORICAL_ONLY_SITES[site]
        return {
            "Group": group,
            "Station": name,
            "USGS": site,
            "Stage ft": None,
            "Flow CFS": None,
            "Raw Stage": None,
            "Raw Flow": None,
            "1h ft": None,
            "6h ft": None,
            "24h ft": None,
            "Age min": None,
            "Age": "—",
            "Status": meta["status"],
            "Stage age": "—",
            "Stage status": meta["status"],
            "Flow age": "—",
            "Flow status": meta["status"],
            "Observation": meta["observation"],
            "Observation dt": None,
            "Note": meta["note"],
        }

    stage_rec = latest_by_key.get((site, PARAM_STAGE))
    flow_rec = latest_by_key.get((site, PARAM_FLOW))

    stage_points = history_by_key.get((site, PARAM_STAGE), [])
    flow_points = history_by_key.get((site, PARAM_FLOW), [])

    stage = (
        stage_rec["value"]
        if stage_rec
        else (stage_points[-1][1] if stage_points else None)
    )
    flow = (
        flow_rec["value"]
        if flow_rec
        else (flow_points[-1][1] if flow_points else None)
    )

    stage_time = (
        stage_rec["time"]
        if stage_rec
        else (stage_points[-1][0] if stage_points else None)
    )
    flow_time = (
        flow_rec["time"]
        if flow_rec
        else (flow_points[-1][0] if flow_points else None)
    )

    newest = max(
        [t for t in (stage_time, flow_time) if t is not None],
        default=None,
    )

    stage_age = age_minutes(stage_time)
    flow_age = age_minutes(flow_time)
    stage_status = status_from_age(stage_age)
    flow_status = status_from_age(flow_age)

    # Overall row status reflects the freshest parameter, but each parameter
    # is independently hidden if its own observation is historical.
    available_ages = [a for a in (stage_age, flow_age) if a is not None]
    freshest_age = min(available_ages) if available_ages else None
    status = status_from_age(freshest_age)

    display_stage = None if stage_status == "HISTORICAL" else stage
    display_flow = None if flow_status == "HISTORICAL" else flow

    return {
        "Group": group,
        "Station": name,
        "USGS": site,
        "Stage ft": display_stage,
        "Flow CFS": display_flow,
        "Raw Stage": stage,
        "Raw Flow": flow,
        "1h ft": stage_change(stage_points, 1),
        "6h ft": stage_change(stage_points, 6),
        "24h ft": stage_change(stage_points, 24),
        "Age min": freshest_age,
        "Age": pretty_age(freshest_age),
        "Status": status,
        "Stage age": pretty_age(stage_age),
        "Stage status": stage_status,
        "Flow age": pretty_age(flow_age),
        "Flow status": flow_status,
        "Observation": format_observation(newest),
        "Observation dt": newest,
        "Note": NOTES.get(site, ""),
    }


rows = []
for site, name in UPPER_BRANCHES.items():
    rows.append(make_row(site, name, "Upper Rock"))
for site, name in MAINSTEM.items():
    rows.append(make_row(site, name, "Main stem"))
for site, name in TRIBUTARIES.items():
    rows.append(make_row(site, name, "Tributary"))

df = pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

now_ct = datetime.now(CENTRAL)
fresh_times = [
    dt for dt in df["Observation dt"]
    if dt is not None and not pd.isna(dt)
]
newest_observation = max(fresh_times) if fresh_times else None

st.title("🦆 Rock River Live")
st.caption(
    f"Official USGS Water Data API • auto-refresh 5 min • build {BUILD} • "
    f"page checked {now_ct.strftime('%I:%M %p %Z')}"
)

if newest_observation:
    st.caption(
        "Newest USGS observation on the dashboard: "
        + format_observation(newest_observation)
    )

leb = df[df["USGS"] == LEBANON_SITE].iloc[0]
stage = leb["Stage ft"]
flow = leb["Flow CFS"]

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Lebanon stage",
    "—" if pd.isna(stage) else f"{stage:.2f} ft",
    metric_delta(leb["1h ft"]),
)

c2.metric(
    "Lebanon flow",
    "—" if pd.isna(flow) else f"{flow:,.0f} CFS",
)

if pd.isna(stage):
    duck_value = "NO DATA"
    duck_label = "Duck-water status"
elif stage >= 9.0:
    duck_value = "LAKE EVERYWHERE"
    duck_label = "Duck-water status • 9.0+ ft"
elif stage >= 7.5:
    duck_value = "STRONG FLOOD"
    duck_label = "Duck-water status • 7.5+ ft"
elif stage >= 7.0:
    duck_value = "GOOD DUCK WATER"
    duck_label = "Duck-water status • 7.0+ ft"
else:
    duck_value = "BELOW 7.0"
    duck_label = "Duck-water status"

c3.metric(duck_label, duck_value)

c4.metric(
    "Lebanon data age",
    leb["Age"],
)

impact = next_lebanon_impact(stage)

if impact is not None:
    if impact["level"] is not None:
        st.info(
            f"**Next NOAA/NWS listed impact:** {impact['level']:.1f} ft — "
            f"{impact['description']}  \\n"
            f"**{impact['difference']:.2f} ft below that level.**"
        )
    else:
        st.warning(f"**Flood impacts:** {impact['description']}.")

source_col1, source_col2 = st.columns(2)
with source_col1:
    st.link_button(
        "Open USGS Lebanon gauge",
        station_link(LEBANON_SITE),
        use_container_width=True,
    )
with source_col2:
    st.link_button(
        "Open NOAA/NWS Lebanon gauge",
        NOAA_LEBANON_URL,
        use_container_width=True,
    )

st.subheader("Lebanon hydrograph")

lebanon_points_7d = lebanon_7d_by_key.get((LEBANON_SITE, PARAM_STAGE), [])
latest_lebanon_stage_record = latest_by_key.get((LEBANON_SITE, PARAM_STAGE))

full_impact_scale = st.checkbox(
    "Show full 8–14 ft flood-impact scale",
    value=False,
)

lebanon_fig = build_lebanon_hydrograph(
    lebanon_points_7d,
    latest_lebanon_stage_record,
    full_scale=full_impact_scale,
)

if lebanon_fig is not None:
    st.plotly_chart(lebanon_fig, use_container_width=True)
    st.caption(
        "USGS stage history, displayed in Central Time. "
        "Bankfull and flood-stage markers are from NWS; road/property impacts "
        "are from the NOAA/NWS Lebanon gauge impact table."
    )
else:
    st.info("No 7-day Lebanon stage history was returned by USGS.")

with st.expander("Lebanon NOAA/NWS flood impacts"):
    st.dataframe(
        impact_rows_for_display(),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Stage ft": st.column_config.NumberColumn(format="%.1f"),
            "Flood impact": st.column_config.TextColumn(width="large"),
            "Official marker": st.column_config.TextColumn(width="small"),
        },
    )
    st.caption(
        "NWS identifies 9.0 ft as bankfull and 10.0 ft as flood stage for LEBW3. "
        "Impact wording is based on the current NOAA/NWS gauge page."
    )

# Quick pulse readout.
live_upper = df[
    (df["Group"] == "Upper Rock")
    & (df["Status"].isin(["LIVE", "DELAYED"]))
    & (df["Stage ft"].notna())
]

if not live_upper.empty:
    st.subheader("Upper Rock pulse")
    pulse_cols = st.columns(len(live_upper))
    for col, (_, row) in zip(pulse_cols, live_upper.iterrows()):
        with col:
            col.metric(
                row["Station"],
                f"{row['Stage ft']:.2f} ft",
                None if pd.isna(row["6h ft"]) else f"{row['6h ft']:+.2f} ft / 6h",
            )

st.subheader("East Branch / Theresa Marsh")
st.info(
    "**East Branch Rock River near Mayville (USGS 05424000) is historical.** "
    "USGS discontinued the gauge on October 1, 2011. In the public sources "
    "reviewed for this dashboard, no active public USGS replacement gauge was "
    "found on the East Branch itself. **Rock River at Horicon** is shown as the "
    "nearest live downstream proxy, but it is not a direct substitute because "
    "marsh and dam operations affect downstream water levels."
)
st.caption(
    "Wisconsin DNR manages water levels at Theresa Marsh using the East Branch "
    "Rock River dam and impoundments for wetland and waterfowl habitat."
)

east_col1, east_col2, east_col3 = st.columns(3)
with east_col1:
    st.link_button(
        "Theresa Marsh — Wisconsin DNR",
        THERESA_MARSH_DNR_URL,
        use_container_width=True,
    )
with east_col2:
    st.link_button(
        "Retired East Branch USGS gauge",
        EAST_BRANCH_RETIRED_USGS_URL,
        use_container_width=True,
    )
with east_col3:
    st.link_button(
        "Live downstream proxy — Horicon",
        station_link("05424057"),
        use_container_width=True,
    )


def show_group(title, group_name):
    st.subheader(title)

    view = df[df["Group"] == group_name].copy()

    # Keep live/recent stations at the top.
    status_order = {
        "LIVE": 0,
        "DELAYED": 1,
        "OLD": 2,
        "NO DATA": 3,
        "HISTORICAL": 4,
    }
    view["_order"] = view["Status"].map(status_order).fillna(9)
    view = view.sort_values(["_order", "Station"])

    view["Stage ft"] = view["Stage ft"].map(
        lambda x: None if pd.isna(x) else round(x, 2)
    )
    view["Flow CFS"] = view["Flow CFS"].map(
        lambda x: None if pd.isna(x) else round(x)
    )
    view["1h"] = view["1h ft"].map(fmt_delta)
    view["6h"] = view["6h ft"].map(fmt_delta)
    view["24h"] = view["24h ft"].map(fmt_delta)
    view["Gauge"] = view["USGS"].map(station_link)

    st.dataframe(
        view[
            [
                "Station",
                "Stage ft",
                "Flow CFS",
                "1h",
                "6h",
                "24h",
                "Age",
                "Status",
                "Observation",
                "Note",
                "Gauge",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Station": st.column_config.TextColumn(width="large"),
            "Stage ft": st.column_config.NumberColumn(format="%.2f"),
            "Flow CFS": st.column_config.NumberColumn(format="%d"),
            "Age": st.column_config.TextColumn(width="small"),
            "Status": st.column_config.TextColumn(width="small"),
            "Observation": st.column_config.TextColumn(width="medium"),
            "Note": st.column_config.TextColumn(width="medium"),
            "Gauge": st.column_config.LinkColumn("USGS", display_text="Open"),
        },
    )


show_group("Upper Rock branches / headwaters", "Upper Rock")
show_group("Rock River main stem / lake", "Main stem")
show_group("Major tributaries", "Tributary")

# ---------------------------------------------------------------------------
# Trend chart
# ---------------------------------------------------------------------------

st.subheader("Gauge trend — last 48 hours")

chart_sites = []
for site, name in STATIONS.items():
    if history_by_key.get((site, PARAM_STAGE)):
        chart_sites.append((site, name))

chart_name_to_site = {name: site for site, name in chart_sites}
default_index = 0
for i, (_, name) in enumerate(chart_sites):
    if "Lebanon" in name:
        default_index = i
        break

if chart_sites:
    selected_name = st.selectbox(
        "Stage chart",
        [name for _, name in chart_sites],
        index=default_index,
    )
    selected_site = chart_name_to_site[selected_name]
    points = history_by_key.get((selected_site, PARAM_STAGE), [])

    chart_df = pd.DataFrame(points, columns=["Time", "Stage ft"])
    chart_df["Time"] = chart_df["Time"].map(lambda x: central_time(x))
    chart_df = chart_df.set_index("Time")

    st.line_chart(chart_df, y="Stage ft", height=340)
    st.caption("Chart times are Central Time (CST/CDT).")
else:
    st.info("No recent stage history was returned.")

# ---------------------------------------------------------------------------
# Old / historical station explanation
# ---------------------------------------------------------------------------

historical = df[df["Status"].isin(["HISTORICAL", "NO DATA"])]

if not historical.empty:
    with st.expander(
        f"Inactive / historical gauges ({len(historical)})",
        expanded=False,
    ):
        st.write(
            "These stations are retained because they are hydrologically useful, "
            "but they are not active live gauges. Years-old values are intentionally "
            "hidden so they cannot be mistaken for current river conditions."
        )
        for _, row in historical.iterrows():
            st.write(
                f"**{row['Station']}** — {row['Status']} — "
                f"last observation: {row['Observation']}"
            )

all_failures = {
    site: message
    for site, message in (
        latest_failures + history_failures + lebanon_7d_failures
    )
}

if all_failures:
    with st.expander(
        f"USGS API issues ({len(all_failures)})",
        expanded=False,
    ):
        st.write(
            "The dashboard stayed online; these individual stations had an API issue:"
        )
        for site, message in all_failures.items():
            st.write(f"**{STATIONS.get(site, site)} ({site})**")
            st.code(message[:1200])

with st.expander("About / legend"):
    st.write(
        "**LIVE** = newest stage or flow observation is 90 minutes old or less.  "
        "**DELAYED** = 90 minutes to 24 hours.  "
        "**OLD** = 1 to 7 days.  "
        "**HISTORICAL** = more than 7 days old."
    )
    st.write(
        "Current readings and recent history come directly from the modern USGS "
        "Water Data API. USGS data are provisional and may be revised."
    )
    st.write(
        "Local hunting-use benchmarks are shown separately from official flood "
        "markers: **7.0 ft** = good duck-water benchmark, **7.5 ft** = stronger "
        "local flooding, and **9.0 ft** = the local 'lake everywhere' benchmark."
    )
    st.write(
        "Official NWS markers for Lebanon (LEBW3 / USGS 05424157): "
        "**9.0 ft bankfull** and **10.0 ft flood stage**."
    )
    st.write(
        "The East Branch near Mayville gauge (USGS 05424000) was discontinued "
        "10/01/2011. Horicon is used only as a **downstream proxy**, not as a "
        "direct East Branch reading."
    )
    st.write("All displayed observation times are **Central Time (CST/CDT)**.")

b1, b2 = st.columns(2)

with b1:
    if st.button("Refresh USGS data now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with b2:
    if st.button("Log out", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()
