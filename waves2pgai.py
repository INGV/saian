from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, FormatStrFormatter, AutoMinorLocator
from obspy import Stream, Trace, UTCDateTime, read_inventory
from obspy.clients.fdsn import Client
from obspy.core.inventory import Inventory
from obspy.geodetics import locations2degrees, kilometers2degrees, degrees2kilometers
from pyrocko import cake


# ============================================================
# DATACLASS E CONFIGURAZIONI
# ============================================================

@dataclass
class StationRequest:
    network: str
    station: str
    channel_prefix: str
    location: str = "*"

    @property
    def waveform_channel_pattern(self) -> str:
        cp = self.channel_prefix.strip()
        if "*" in cp or "?" in cp:
            return cp
        if len(cp) == 2:
            return f"{cp}?"
        return cp

    @property
    def stationxml_channel_pattern(self) -> str:
        cp = self.channel_prefix.strip()
        if "*" in cp or "?" in cp:
            return cp
        return f"{cp}*"

@dataclass
class EventInfo:
    origin_time_iso: str
    latitude: float
    longitude: float
    depth_km: float
    pick_p_iso: Optional[str] = None
    pick_s_iso: Optional[str] = None
    event_id: Optional[str] = None
    origin_id: Optional[str] = None

@dataclass
class FilterDef:
    type: str  # bp, hp, lp
    freqmin: Optional[float]
    freqmax: Optional[float]
    corners: int
    mandatory: bool = False

    def to_label(self) -> str:
        co1 = f"{self.freqmin}" if self.freqmin is not None else ""
        co2 = f"{self.freqmax}" if self.freqmax is not None else ""
        mand_str = " (MANDATORY)" if self.mandatory else ""
        return f"Filtered,{self.type.upper()},{co1},{co2},{self.corners}{mand_str}"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "freqmin": self.freqmin,
            "freqmax": self.freqmax,
            "corners": self.corners,
            "mandatory": self.mandatory
        }

@dataclass
class DownloadConfig:
    t_before_origin: float = 30.0
    t_after_origin: float = 180.0
    zoom_half_width_s: float = 5.0
    output_dir: str = "output_waveforms"
    base_url: str = "https://webservices.ingv.it"
    user: Optional[str] = None
    password: Optional[str] = None

@dataclass
class PlotConfig:
    figsize: tuple[float, float] = (20.0, 10.0)
    dpi: int = 800
    formats: tuple[str, ...] = ("png", "pdf", "svg")
    line_width: float = 0.7
    full_major_tick_s: float = 5.0
    full_minor_tick_s: float = 0.5
    zoom_major_tick_s: float = 0.1
    zoom_minor_tick_s: float = 0.01
    draw_minor_grid: bool = True
    draw_major_grid: bool = True
    pick_line_width: float = 1.2
    expand_dynamics_enabled: bool = False
    expand_window_s: float = 0.5

@dataclass
class TravelTimeConfig:
    enabled: bool = True
    model_name: str = "ak135-f-continental.m"
    receiver_depth_km: float = 0.0

@dataclass
class ZoomLevelPreset:
    half_width_s: float
    major_tick_s: float
    minor_tick_s: float


DEFAULT_CONFIG = {
    "download": {
        "t_before_origin": 20.0,
        "t_after_origin": 120.0,
        "zoom_half_width_s": 3.0,
        "output_dir": "waveforms_event_001",
        "base_url": "https://webservices.ingv.it",
        "user": None,
        "password": None,
    },
    "plotting": {
        "figsize": [20, 10],
        "dpi": 800,
        "formats": ["png", "pdf", "svg"],
        "line_width": 0.7,
        "full_major_tick_s": 5.0,
        "full_minor_tick_s": 0.5,
        "zoom_major_tick_s": 0.1,
        "zoom_minor_tick_s": 0.01,
        "draw_minor_grid": True,
        "draw_major_grid": True,
        "pick_line_width": 1.2,
        "expand_dynamics": {
            "enabled": False,
            "window_s": 0.5
        },
    },
    "travel_time": {
        "enabled": True,
        "model_name": "ak135-f-continental.m",
        "receiver_depth_km": 0.0
    },
    "zoom_levels": {
        "context": {"half_width_s": 3.0, "major_tick_s": 0.5, "minor_tick_s": 0.1},
        "fine": {"half_width_s": 1.0, "major_tick_s": 0.1, "minor_tick_s": 0.02},
        "ultrafine": {"half_width_s": 0.4, "major_tick_s": 0.1, "minor_tick_s": 0.02}
    }
}


# ============================================================
# UTIL E PROCESSING
# ============================================================

def ensure_utc(value: str | UTCDateTime | None) -> Optional[UTCDateTime]:
    if value is None: return None
    if isinstance(value, UTCDateTime): return value
    return UTCDateTime(value)

def safe_loc(loc: str) -> str:
    return loc if loc and loc != "*" else "--"

def station_tag(network: str, station: str, location: str, channel_prefix: str) -> str:
    return f"{network}.{station}.{safe_loc(location)}.{channel_prefix}"

def channel_filename(trace: Trace, event: EventInfo, cut_type: str) -> str:
    loc = safe_loc(trace.stats.location)
    eid = event.event_id if event.event_id else "manual"
    oid = event.origin_id if event.origin_id else "manual"
    return f"eid{eid}_oid{oid}_{trace.stats.network}.{trace.stats.station}.{loc}.{trace.stats.channel}_{cut_type}.mseed"

def stationxml_cache_filename(net: str, sta: str, loc: str) -> str:
    return f"{net}.{sta}.{safe_loc(loc)}.xml"

def get_relative_time_axis(trace: Trace) -> np.ndarray:
    return np.linspace(0, trace.stats.npts * trace.stats.delta, trace.stats.npts, endpoint=False)

def pick_relative_seconds(trace: Trace, pick_time: UTCDateTime) -> float:
    return float(pick_time - trace.stats.starttime)

def preprocess_trace_for_plot(trace: Trace, filter_def: Optional[FilterDef] = None) -> Trace:
    tr = trace.copy()
    tr.detrend("demean")
    tr.detrend("linear")
    
    if filter_def:
        if filter_def.type == "bp":
            tr.filter("bandpass", freqmin=filter_def.freqmin, freqmax=filter_def.freqmax, corners=filter_def.corners, zerophase=True)
        elif filter_def.type == "hp":
            tr.filter("highpass", freq=filter_def.freqmax, corners=filter_def.corners, zerophase=True)
        elif filter_def.type == "lp":
            tr.filter("lowpass", freq=filter_def.freqmin, corners=filter_def.corners, zerophase=True)
            
    return tr

def group_3c_for_plot(stream: Stream) -> Stream:
    preferred_order = {"Z": 0, "N": 1, "1": 1, "E": 2, "2": 2}
    traces = list(stream)
    traces.sort(key=lambda tr: preferred_order.get(tr.stats.channel[-1], 99))
    return Stream(traces=traces[:3])

def deep_update(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_update(result[k], v)
        else:
            result[k] = v
    return result

def load_json_config(path: Optional[str]) -> dict:
    if path is None:
        return DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    return deep_update(DEFAULT_CONFIG, user_cfg)

def load_ai_picks_json(path: Optional[str]) -> dict[tuple[str, str, str], dict]:
    if path is None:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for item in data.get("stations", []):
        net = item["network"].strip()
        sta = item["stacode"].strip()
        cha = item["channel_code"].strip()
        # Some AI models report the specific component channel they picked on
        # (e.g. "HHZ") instead of the requested 2-letter band/instrument code
        # (e.g. "HH"), which is how StationRequest.channel_prefix always looks
        # up entries here. Normalize so both forms match the same station.
        if len(cha) > 2:
            cha = cha[:2]
        result[(net, sta, cha)] = item
    print(f"[OK] caricati {len(result)} pick AI dal file {path}")
    return result

def build_configs(cfg_dict: dict) -> tuple[DownloadConfig, PlotConfig, TravelTimeConfig]:
    d = cfg_dict.get("download", {})
    p = cfg_dict.get("plotting", {})
    dcfg = DownloadConfig(
        t_before_origin=float(d.get("t_before_origin", 20.0)),
        t_after_origin=float(d.get("t_after_origin", 120.0)),
        zoom_half_width_s=float(d.get("zoom_half_width_s", 3.0)),
        output_dir=str(d.get("output_dir", "waveforms_event_001")),
        base_url=str(d.get("base_url", "https://webservices.ingv.it")),
        user=d.get("user"), password=d.get("password"),
    )
    ed = p.get("expand_dynamics", {})
    pcfg = PlotConfig(
        figsize=(float(p.get("figsize", [20, 10])[0]), float(p.get("figsize", [20, 10])[1])),
        dpi=int(p.get("dpi", 800)),
        formats=tuple(str(x).lower() for x in p.get("formats", ["png", "pdf", "svg"])),
        line_width=float(p.get("line_width", 0.7)),
        full_major_tick_s=float(p.get("full_major_tick_s", 5.0)),
        full_minor_tick_s=float(p.get("full_minor_tick_s", 0.5)),
        zoom_major_tick_s=float(p.get("zoom_major_tick_s", 0.1)),
        zoom_minor_tick_s=float(p.get("zoom_minor_tick_s", 0.01)),
        draw_minor_grid=bool(p.get("draw_minor_grid", True)),
        draw_major_grid=bool(p.get("draw_major_grid", True)),
        pick_line_width=float(p.get("pick_line_width", 1.2)),
        expand_dynamics_enabled=bool(ed.get("enabled", False)),
        expand_window_s=float(ed.get("window_s", 0.5)),
    )
    t = cfg_dict.get("travel_time", {})
    tcfg = TravelTimeConfig(
        enabled=bool(t.get("enabled", True)),
        model_name=str(t.get("model_name", "ak135-f-continental.m")),
        receiver_depth_km=float(t.get("receiver_depth_km", 0.0)),
    )
    return dcfg, pcfg, tcfg

def load_zoom_level_presets(cfg_dict: dict) -> dict[str, ZoomLevelPreset]:
    defaults = {
        "context": ZoomLevelPreset(half_width_s=3.0, major_tick_s=0.5, minor_tick_s=0.1),
        "fine": ZoomLevelPreset(half_width_s=1.0, major_tick_s=0.1, minor_tick_s=0.02),
        "ultrafine": ZoomLevelPreset(half_width_s=0.4, major_tick_s=0.1, minor_tick_s=0.02),
    }
    zcfg = cfg_dict.get("zoom_levels", {})
    out: dict[str, ZoomLevelPreset] = {}
    for name, default in defaults.items():
        item = zcfg.get(name, {})
        out[name] = ZoomLevelPreset(
            half_width_s=float(item.get("half_width_s", default.half_width_s)),
            major_tick_s=float(item.get("major_tick_s", default.major_tick_s)),
            minor_tick_s=float(item.get("minor_tick_s", default.minor_tick_s)),
        )
    return out

def parse_filter_arg(filter_arg: Optional[str], ai_entry: Optional[dict]) -> Optional[FilterDef]:
    if not filter_arg:
        return None
    filter_arg = filter_arg.strip()

    if filter_arg.lower() == "suggested":
        if not ai_entry or "suggested_bpfilter" not in ai_entry:
            print("[WARN] Filtro 'suggested' richiesto ma non trovato nel JSON AI. Applico dati grezzi.")
            return None

        bp = ai_entry["suggested_bpfilter"]
        is_mandatory = bool(bp.get("mandatory", False))

        if not is_mandatory:
            print("[INFO] Filtro 'suggested' ignorato per questa stazione perché 'mandatory' è False.")
            return None

        return FilterDef(
            type="bp",
            freqmin=float(bp["lower_corner"]),
            freqmax=float(bp["upper_corner"]),
            corners=int(bp["number_of_poles"]),
            mandatory=is_mandatory
        )
        
    parts = filter_arg.split(",")
    if len(parts) != 4:
        raise ValueError("Il filtro deve essere 'suggested' o 'tipo,co1,co2,np' (es. bp,1.0,15.0,4 o hp,,1.0,4)")
        
    ftype, co1_str, co2_str, np_str = [p.strip() for p in parts]
    ftype = ftype.lower()
    corners = int(np_str)
    
    if ftype == "bp":
        return FilterDef("bp", float(co1_str), float(co2_str), corners, False)
    elif ftype == "hp":
        return FilterDef("hp", None, float(co2_str), corners, False)
    elif ftype == "lp":
        return FilterDef("lp", float(co1_str), None, corners, False)
    else:
        raise ValueError(f"Tipo filtro sconosciuto: {ftype}")


# ============================================================
# PARSING CLI
# ============================================================

def parse_zoom_levels_arg(value: Optional[str]) -> list[str]:
    if value is None or not value.strip(): return ["single"]
    tokens = [x.strip().lower() for x in value.split(",") if x.strip()]
    if "all" in tokens: tokens = ["context", "fine", "ultrafine"]
    valid = {"single", "context", "fine", "ultrafine"}
    invalid = [x for x in tokens if x not in valid]
    if invalid: raise ValueError(f"Livelli zoom non validi: {invalid}.")
    out: list[str] = []
    for token in tokens:
        if token not in out: out.append(token)
    return out

def iter_requested_zoom_specs(
        zoom_levels: list[str],
        download_cfg: DownloadConfig,
        plot_cfg: PlotConfig,
        zoom_level_presets: dict[str, ZoomLevelPreset],
) -> list[tuple[str, float, float, float]]:
    out = []
    for level_name in zoom_levels:
        if level_name == "single":
            out.append(("single", float(download_cfg.zoom_half_width_s), float(plot_cfg.zoom_major_tick_s), float(plot_cfg.zoom_minor_tick_s)))
        else:
            preset = zoom_level_presets[level_name]
            out.append((level_name, float(preset.half_width_s), float(preset.major_tick_s), float(preset.minor_tick_s)))
    return out

def parse_event_arg(event_arg: str) -> EventInfo:
    parts = [x.strip() for x in event_arg.split(",")]
    if len(parts) != 4:
        raise ValueError("--event deve essere nel formato 'OT,LAT,LON,DEP'")
    return EventInfo(
        origin_time_iso=parts[0], latitude=float(parts[1]), longitude=float(parts[2]),
        depth_km=float(parts[3]), event_id="manual", origin_id="manual"
    )

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scarica waveform INGV per AI picking.")
    parser.add_argument("--eventid", required=False, default=None)
    parser.add_argument("--originid", required=False, default=None)
    parser.add_argument("--event", required=False, default=None)
    parser.add_argument("--networks", required=False, default=None)
    parser.add_argument("--distances", required=False, default=None)
    parser.add_argument("--channels", required=False, default="*")
    parser.add_argument("--stations", required=False)
    parser.add_argument("--pick-p", default=None)
    parser.add_argument("--pick-s", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--write-default-config", default=None)
    parser.add_argument("--version", action="version", version="waves2pgai 0.3")
    parser.add_argument("--zoom", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--plot-picks", action="store_true")
    parser.add_argument("--ai-picks-json", default=None)
    parser.add_argument("--zoom-levels", default=None)
    parser.add_argument("--expand-dynamics", action="store_true", help="Amplifica il segnale ed abbatte il rumore preservando le proporzioni fisiche tra le componenti")
    parser.add_argument("--expand-window", type=float, default=None, help="Finestra RMS in secondi per il calcolo dell'inviluppo di espansione")
    parser.add_argument("--filter", required=False, default=None, help="Es: 'suggested' o 'bp,1.0,15.0,4'")
    return parser

def parse_stations_arg(stations_arg: str) -> list[StationRequest]:
    result: list[StationRequest] = []
    for token in stations_arg.split(","):
        token = token.strip()
        if not token: continue
        parts = token.split(".")
        if len(parts) == 3:
            net, sta, ch = parts; loc = "*"
        elif len(parts) == 4:
            net, sta, loc, ch = parts; loc = "" if loc == "--" else loc
        else: raise ValueError(f"Stazione non valida: '{token}'")
        result.append(StationRequest(network=net, station=sta, location=loc, channel_prefix=ch))
    return result


# ============================================================
# CLIENT E DOWNLOAD
# ============================================================

def build_client(cfg: DownloadConfig) -> Client:
    if cfg.user and cfg.password: return Client(base_url=cfg.base_url, user=cfg.user, password=cfg.password)
    return Client(base_url=cfg.base_url)

def fetch_event_info_from_fdsn(client: Client, eventid: str, originid: Optional[str] = None) -> EventInfo:
    print(f"[FDSN] Download dati evento eventid={eventid} (includeallorigins=True)...")
    try:
        cat = client.get_events(eventid=eventid, includeallorigins=True, includeallmagnitudes=True)
    except Exception as e: raise ValueError(f"Impossibile scaricare evento FDSN: {e}")
    if not cat: raise ValueError(f"Nessun evento trovato")

    ev = cat[0]
    selected_origin = None
    if originid:
        for org in ev.origins:
            if originid in str(org.resource_id):
                selected_origin = org; break
        if not selected_origin: raise ValueError(f"Origin ID '{originid}' non trovato.")
    else:
        selected_origin = ev.preferred_origin or ev.origins[0]

    actual_origin_id = str(selected_origin.resource_id).split('=')[-1]
    depth_km = (selected_origin.depth / 1000.0) if selected_origin.depth is not None else 0.0
    return EventInfo(
        origin_time_iso=str(selected_origin.time), latitude=selected_origin.latitude,
        longitude=selected_origin.longitude, depth_km=depth_km, event_id=eventid, origin_id=actual_origin_id
    )

def fetch_stations_by_distance(client: Client, event: EventInfo, networks: str, distances_str: str, channels: str) -> list[StationRequest]:
    try: min_km, max_km = map(float, distances_str.split(","))
    except ValueError: raise ValueError("Parametro --distances non valido. Es: '0,50'")

    minradius_deg, maxradius_deg = kilometers2degrees(min_km), kilometers2degrees(max_km)
    try:
        inv = client.get_stations(network=networks, latitude=event.latitude, longitude=event.longitude,
                                  minradius=minradius_deg, maxradius=maxradius_deg, level="station")
    except Exception as e: raise ValueError(f"Ricerca spaziale fallita: {e}")

    reqs = []
    requested_channels = [c.strip() for c in channels.split(",") if c.strip()] or ["*"]
    for net in inv:
        for sta in net:
            for ch in requested_channels:
                reqs.append(StationRequest(network=net.code, station=sta.code, location="*", channel_prefix=ch))
    return reqs

def download_station_stream(client: Client, station_req: StationRequest, starttime: UTCDateTime, endtime: UTCDateTime) -> Stream:
    loc = station_req.location if station_req.location != "" else "*"
    st = client.get_waveforms(network=station_req.network, station=station_req.station, location=loc, channel=station_req.waveform_channel_pattern, starttime=starttime, endtime=endtime, attach_response=False)
    st.trim(starttime=starttime, endtime=endtime, nearest_sample=False)
    st.merge(method=1, fill_value="interpolate")
    st.sort()
    return st

def download_stationxml_full(client: Client, station_req: StationRequest) -> Inventory:
    loc = station_req.location if station_req.location != "" else "*"
    return client.get_stations(network=station_req.network, station=station_req.station, location=loc, level="response", format="xml")

def save_stationxml(inv: Inventory, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    inv.write(str(out_file), format="STATIONXML")

def get_or_load_stationxml(client: Client, station_req: StationRequest, cache_dir: Path) -> tuple[Inventory, Path, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    xml_path = cache_dir / stationxml_cache_filename(station_req.network, station_req.station, station_req.location)
    if xml_path.exists(): return read_inventory(str(xml_path)), xml_path, True
    inv = download_stationxml_full(client, station_req)
    save_stationxml(inv, xml_path)
    return inv, xml_path, False

def get_station_coordinates(inv: Inventory) -> tuple[float, float, float]:
    if len(inv.networks) == 0 or len(inv.networks[0].stations) == 0: raise ValueError("Inventory vuoto")
    sta = inv.networks[0].stations[0]
    return float(sta.latitude), float(sta.longitude), float(sta.elevation)

def load_cake_model_safe(model_name: str):
    try: return cake.load_model(model_name)
    except Exception as exc: return cake.load_model()

def theoretical_phase_pick(model, origin: UTCDateTime, event_lat: float, event_lon: float, event_depth_km: float, station_lat: float, station_lon: float, phase_name: str, tt_cfg: TravelTimeConfig) -> Optional[UTCDateTime]:
    dist_deg = locations2degrees(lat1=event_lat, long1=event_lon, lat2=station_lat, long2=station_lon)
    candidates = ["Pg", "p", "P"] if phase_name.upper() == "P" else ["Sg", "s", "S"] if phase_name.upper() == "S" else [phase_name]
    best_ray = None
    for cname in candidates:
        try:
            phases = cake.PhaseDef.classic(cname)
            rays = model.arrivals(phases=phases, distances=[dist_deg], zstart=event_depth_km * 1000.0, zstop=tt_cfg.receiver_depth_km * 1000.0)
            if rays:
                first = sorted(rays, key=lambda r: r.t)[0]
                if best_ray is None or first.t < best_ray.t: best_ray = first
        except: continue
    return origin + float(best_ray.t) if best_ray else None

def save_per_channel_mseed(stream: Stream, station_out_dir: Path, event: EventInfo, cut_type: str) -> None:
    station_out_dir.mkdir(parents=True, exist_ok=True)
    for tr in stream:
        out_file = station_out_dir / channel_filename(tr, event, cut_type)
        tr.write(str(out_file), format="MSEED")


# ============================================================
# PLOTTING E LOGICA ASIMMETRICA
# ============================================================

def style_time_axis(ax, major_tick_s: float, minor_tick_s: float, draw_major_grid: bool, draw_minor_grid: bool) -> None:
    ax.xaxis.set_major_locator(MultipleLocator(major_tick_s))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.3f"))
    ax.xaxis.set_minor_locator(MultipleLocator(minor_tick_s))
    ax.tick_params(axis="x", which="major", length=8, width=0.8, labelsize=9)
    ax.tick_params(axis="x", which="minor", length=4, width=0.6, labelbottom=False)
    ax.grid(False, which="major", axis="x")
    ax.grid(False, which="minor", axis="x")
    if draw_major_grid: ax.grid(True, which="major", axis="x", alpha=0.30)
    if draw_minor_grid: ax.grid(True, which="minor", axis="x", alpha=0.12)

def add_pick_lines(ax, tr: Trace, p_pick: Optional[UTCDateTime], s_pick: Optional[UTCDateTime], line_width: float) -> None:
    if p_pick is not None:
        xp = pick_relative_seconds(tr, p_pick)
        ax.axvline(xp, color="tab:red", lw=line_width, ls="--", alpha=0.95)
        ax.text(xp, 0.97, "P", color="tab:red", transform=ax.get_xaxis_transform(), ha="left", va="top", fontsize=9, fontweight="bold")
    if s_pick is not None:
        xs = pick_relative_seconds(tr, s_pick)
        ax.axvline(xs, color="tab:blue", lw=line_width, ls="--", alpha=0.95)
        ax.text(xs, 0.97, "S", color="tab:blue", transform=ax.get_xaxis_transform(), ha="left", va="top", fontsize=9, fontweight="bold")

def save_figure_multi_format(fig, basepath_no_ext: Path, formats: Iterable[str], dpi: int) -> list[Path]:
    saved_files: list[Path] = []
    for fmt in formats:
        out_file = basepath_no_ext.parent / f"{basepath_no_ext.name}.{fmt}"
        if fmt.lower() == "png": fig.savefig(out_file, dpi=dpi, bbox_inches="tight")
        else: fig.savefig(out_file, bbox_inches="tight", format=fmt.lower())
        saved_files.append(out_file)
    return saved_files

def first_sample_time_for_stream(stream: Stream) -> UTCDateTime:
    st = group_3c_for_plot(stream)
    return min(tr.stats.starttime for tr in st)

def last_sample_time_for_stream(stream: Stream) -> UTCDateTime:
    st = group_3c_for_plot(stream)
    return max(tr.stats.endtime for tr in st)

def relative_seconds_from_first_sample(stream: Stream, abs_time: UTCDateTime) -> float:
    return float(abs_time - first_sample_time_for_stream(stream))

def trace_descriptors_for_metadata(stream: Stream) -> list[dict]:
    st = group_3c_for_plot(stream)
    out = []
    for tr in st:
        out.append({
            "id": tr.id, "channel": tr.stats.channel, "starttime": tr.stats.starttime.isoformat(),
            "endtime": tr.stats.endtime.isoformat(), "npts": int(tr.stats.npts),
            "sample_rate_hz": float(tr.stats.sampling_rate), "delta_s": float(tr.stats.delta)
        })
    return out

def write_plot_metadata(basepath_no_ext: Path, saved_files: list[Path], station_req: StationRequest, event: EventInfo,
                        stream: Stream, plot_cfg: PlotConfig, plot_type: str, tick_major_s: float, tick_minor_s: float,
                        window_start_relative_s: float, window_end_relative_s: float, plot_picks: bool,
                        filter_def: Optional[FilterDef], zoom_level: Optional[str] = None,
                        zoom_reference_phase: Optional[str] = None, zoom_reference_time: Optional[UTCDateTime] = None) -> None:
    st = group_3c_for_plot(stream)
    if len(st) == 0: return

    first_sample = first_sample_time_for_stream(st)
    last_sample = last_sample_time_for_stream(st)
    origin = ensure_utc(event.origin_time_iso)

    metadata = {
        "files": [p.name for p in saved_files],
        "station": {"network": station_req.network, "stacode": station_req.station,
                    "location": safe_loc(station_req.location), "channel_code": station_req.channel_prefix},
        "plot": {
            "plot_type": plot_type, "zoom_level": zoom_level, "x_axis_reference": "relative_to_first_sample",
            "first_sample_time": first_sample.isoformat(), "last_sample_time": last_sample.isoformat(),
            "origin_time": origin.isoformat() if origin is not None else None,
            "window_start_relative_s": float(window_start_relative_s),
            "window_end_relative_s": float(window_end_relative_s),
            "duration_s": float(window_end_relative_s - window_start_relative_s),
            "zoom_reference_phase": zoom_reference_phase,
            "zoom_reference_time": zoom_reference_time.isoformat() if zoom_reference_time is not None else None,
            "tick_major_s": float(tick_major_s), "tick_minor_s": float(tick_minor_s), "plot_picks": bool(plot_picks)
        },
        "sampling": {"sample_rate_hz": float(st[0].stats.sampling_rate), "delta_s": float(st[0].stats.delta)},
        "channels": [tr.stats.channel for tr in st], "traces": trace_descriptors_for_metadata(st),
        "processing": {
            "filtered": filter_def is not None, 
            "filter": filter_def.to_dict() if filter_def else None,
            "amplitude_processing": {
                "method": "dynamic_expansion", 
                "window_seconds": plot_cfg.expand_window_s, 
                "applied_channels": [tr.stats.channel for tr in st]
            } if plot_cfg.expand_dynamics_enabled else None
        },
    }
    meta_path = basepath_no_ext.parent / f"{basepath_no_ext.name}.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

# ============================================================
# LOGICA FINESTRE ASIMMETRICHE
# ============================================================

def get_asymmetric_windows(phase: str, preset_half: float, ai_entry: Optional[dict], p_pick: Optional[UTCDateTime], s_pick: Optional[UTCDateTime]) -> tuple[float, float]:
    err_pre = 0.0
    err_post = 0.0
    if ai_entry:
        pick_data = ai_entry.get(f"pick_{phase.lower()}", {})
        err_pre = pick_data.get("uncertainty_lower", 0.0)
        err_post = pick_data.get("uncertainty_upper", 0.0)
    
    # DOPO il pick: base preimpostata (poco) + errore stimato
    w_post = preset_half + err_post
    
    # PRIMA del pick: 5 volte il valore (base + errore pre)
    w_pre = 5.0 * (preset_half + err_pre)
    
    # Salvaguardia per non tagliare la P dentro la S
    if phase.upper() == "S" and p_pick is not None and s_pick is not None:
        max_w_pre = float(s_pick - p_pick) - 0.05
        if max_w_pre < 0.0:
            max_w_pre = 0.0
        w_pre = min(w_pre, max_w_pre)
        
    return w_pre, w_post


def plot_full_station(stream: Stream, event: EventInfo, station_req: StationRequest, plot_cfg: PlotConfig,
                      out_basepath_no_ext: Path, p_pick: Optional[UTCDateTime] = None,
                      s_pick: Optional[UTCDateTime] = None, plot_picks: bool = False,
                      filter_def: Optional[FilterDef] = None) -> list[Path]:
    st = group_3c_for_plot(stream)
    if len(st) == 0: return []
    fig, axes = plt.subplots(len(st), 1, figsize=plot_cfg.figsize, sharex=False)
    if len(st) == 1: axes = [axes]

    processed_traces = [preprocess_trace_for_plot(tr, filter_def) for tr in st]
    min_len = min(len(tr.data) for tr in processed_traces)
    x = get_relative_time_axis(processed_traces[0])[:min_len]
    y_data = [tr.data.astype(np.float64)[:min_len] for tr in processed_traces]
    
    # --- LOGICA DI ESPANSIONE DELLA DINAMICA (SHARED ENVELOPE) ---
    if plot_cfg.expand_dynamics_enabled:
        M = np.sqrt(sum(y**2 for y in y_data))
        sample_rate = processed_traces[0].stats.sampling_rate
        window_samples = max(1, int(plot_cfg.expand_window_s * sample_rate))
        kernel = np.ones(window_samples) / window_samples
        E_shared = np.sqrt(np.convolve(np.square(M), kernel, mode='same'))
        
        max_E = np.max(E_shared)
        if max_E > 0:
            E_shared /= max_E
            
        for i in range(len(y_data)):
            y_data[i] = y_data[i] * E_shared
    # -------------------------------------------------------------

    for ax, tr, y in zip(axes, processed_traces, y_data):
        ax.plot(x, y, color="black", lw=plot_cfg.line_width)
        ax.set_ylabel(tr.stats.channel, rotation=0, labelpad=28, fontsize=10)
        ax.margins(x=0)
        style_time_axis(ax, major_tick_s=plot_cfg.full_major_tick_s, minor_tick_s=plot_cfg.full_minor_tick_s,
                        draw_major_grid=plot_cfg.draw_major_grid, draw_minor_grid=plot_cfg.draw_minor_grid)
        if plot_picks: add_pick_lines(ax, tr, p_pick, s_pick, line_width=plot_cfg.pick_line_width)
        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis="y", which="major", labelsize=8)
        ax.tick_params(axis="y", which="minor", length=2)

    axes[-1].set_xlabel("Tempo relativo al primo campione [s]", fontsize=11)
    
    # --- TITOLO CORRETTO E AGGIORNATO ---
    origin = ensure_utc(event.origin_time_iso)
    first_sample = first_sample_time_for_stream(st)
    filter_str = filter_def.to_label() if filter_def else "Not filtered"
    exp_str = "\nDynamic expansion active" if plot_cfg.expand_dynamics_enabled else ""
    
    title = f"{station_req.network}.{station_req.station}.{safe_loc(station_req.location)}.{station_req.channel_prefix}"
    title += f"\nFirst sample: {first_sample.isoformat()}   Origin: {origin.isoformat()}\nLat={event.latitude:.5f} Lon={event.longitude:.5f} Depth={event.depth_km:.2f} km"
    title += f"\n{filter_str}{exp_str}"
    
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    saved_files = save_figure_multi_format(fig, out_basepath_no_ext, plot_cfg.formats, plot_cfg.dpi)
    plt.close(fig)
    return saved_files


def plot_zoom_around_pick(stream: Stream, station_req: StationRequest, pick_time: UTCDateTime, pick_label: str,
                          plot_cfg: PlotConfig, win_pre_s: float, win_post_s: float, out_basepath_no_ext: Path,
                          plot_picks: bool = False, zoom_major_tick_s: Optional[float] = None,
                          zoom_minor_tick_s: Optional[float] = None, filter_def: Optional[FilterDef] = None) -> list[Path]:
    st = group_3c_for_plot(stream)
    if len(st) == 0: return []
    fig, axes = plt.subplots(len(st), 1, figsize=plot_cfg.figsize, sharex=False)
    if len(st) == 1: axes = [axes]

    processed_traces = [preprocess_trace_for_plot(tr, filter_def) for tr in st]
    min_len = min(len(tr.data) for tr in processed_traces)
    x = get_relative_time_axis(processed_traces[0])[:min_len]
    y_data = [tr.data.astype(np.float64)[:min_len] for tr in processed_traces]
    
    x_pick = pick_relative_seconds(processed_traces[0], pick_time)
    xmin = x_pick - win_pre_s
    xmax = x_pick + win_post_s
    mask = (x >= xmin) & (x <= xmax)

    if not np.any(mask):
        for ax, tr in zip(axes, processed_traces):
            ax.text(0.5, 0.5, "Pick fuori dalla finestra scaricata", transform=ax.transAxes, ha="center", va="center", fontsize=11)
            ax.set_ylabel(tr.stats.channel, rotation=0, labelpad=28, fontsize=10)
        plt.close(fig)
        return []

    # --- LOGICA DI ESPANSIONE DELLA DINAMICA LOCALIZZATA SULLO ZOOM ---
    if plot_cfg.expand_dynamics_enabled:
        y_windowed = [y[mask] for y in y_data]
        M = np.sqrt(sum(yw**2 for yw in y_windowed))
        sample_rate = processed_traces[0].stats.sampling_rate
        window_samples = max(1, int(plot_cfg.expand_window_s * sample_rate))
        kernel = np.ones(window_samples) / window_samples
        E_shared = np.sqrt(np.convolve(np.square(M), kernel, mode='same'))
        
        max_E = np.max(E_shared)
        if max_E > 0:
            E_shared /= max_E
            
        for i in range(len(y_data)):
            y_data[i][mask] = y_windowed[i] * E_shared
    # ------------------------------------------------------------------

    for ax, tr, y in zip(axes, processed_traces, y_data):
        ax.plot(x[mask], y[mask], color="black", lw=plot_cfg.line_width)
        if plot_picks:
            ax.axvline(x_pick, color="tab:red" if pick_label.upper() == "P" else "tab:blue", lw=plot_cfg.pick_line_width, ls="--", alpha=0.95)
            ax.text(x_pick, 0.97, pick_label.upper(), color="tab:red" if pick_label.upper() == "P" else "tab:blue", transform=ax.get_xaxis_transform(), ha="left", va="top", fontsize=9, fontweight="bold")

        ax.set_xlim(xmin, xmax)
        ax.set_ylabel(tr.stats.channel, rotation=0, labelpad=28, fontsize=10)
        style_time_axis(ax,
                        major_tick_s=zoom_major_tick_s if zoom_major_tick_s is not None else plot_cfg.zoom_major_tick_s,
                        minor_tick_s=zoom_minor_tick_s if zoom_minor_tick_s is not None else plot_cfg.zoom_minor_tick_s,
                        draw_major_grid=plot_cfg.draw_major_grid, draw_minor_grid=plot_cfg.draw_minor_grid)
        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis="y", which="major", labelsize=8)
        ax.tick_params(axis="y", which="minor", length=2)

    axes[-1].set_xlabel("Tempo relativo al primo campione [s]", fontsize=11)
    
    # --- TITOLO CORRETTO E AGGIORNATO ---
    first_sample = first_sample_time_for_stream(st)
    filter_str = filter_def.to_label() if filter_def else "Not filtered"
    exp_str = "\nDynamic expansion active" if plot_cfg.expand_dynamics_enabled else ""
    
    title = f"{station_req.network}.{station_req.station}.{safe_loc(station_req.location)}.{station_req.channel_prefix}"
    title += f"\nFirst sample: {first_sample.isoformat()}\nZoom {pick_label.upper()} reference: {pick_time.isoformat()}"
    title += f"\n{filter_str}{exp_str}"
    
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    saved_files = save_figure_multi_format(fig, out_basepath_no_ext, plot_cfg.formats, plot_cfg.dpi)
    plt.close(fig)
    return saved_files


def resolve_reference_picks(event: EventInfo, inv: Inventory, tt_cfg: TravelTimeConfig, cake_model,
                            ai_entry: Optional[dict] = None) -> tuple[Optional[UTCDateTime], Optional[UTCDateTime]]:
    origin = ensure_utc(event.origin_time_iso)
    if origin is None: raise ValueError("origin_time_iso obbligatorio")

    p_pick, s_pick = None, None

    if ai_entry is not None:
        p_ai = ai_entry.get("pick_p")
        if p_ai and p_ai.get("time"): p_pick = ensure_utc(p_ai["time"])
        s_ai = ai_entry.get("pick_s")
        if s_ai and s_ai.get("time"): s_pick = ensure_utc(s_ai["time"])

    if p_pick is None: p_pick = ensure_utc(event.pick_p_iso)
    if s_pick is None: s_pick = ensure_utc(event.pick_s_iso)

    if not tt_cfg.enabled: return p_pick, s_pick

    try:
        sta_lat, sta_lon, _ = get_station_coordinates(inv)
    except Exception as exc:
        return p_pick, s_pick

    if p_pick is None:
        p_pick = theoretical_phase_pick(cake_model, origin=origin, event_lat=event.latitude, event_lon=event.longitude,
                                        event_depth_km=event.depth_km, station_lat=sta_lat, station_lon=sta_lon,
                                        phase_name="P", tt_cfg=tt_cfg)
    if s_pick is None:
        s_pick = theoretical_phase_pick(cake_model, origin=origin, event_lat=event.latitude, event_lon=event.longitude,
                                        event_depth_km=event.depth_km, station_lat=sta_lat, station_lon=sta_lon,
                                        phase_name="S", tt_cfg=tt_cfg)

    return p_pick, s_pick


def process_event_stations(event: EventInfo, stations: Iterable[StationRequest], download_cfg: DownloadConfig,
                           plot_cfg: PlotConfig, tt_cfg: TravelTimeConfig, cake_model, ai_picks: dict,
                           zoom_levels: list[str], zoom_level_presets: dict[str, ZoomLevelPreset],
                           filter_arg: Optional[str] = None, make_full: bool = True, make_zoom: bool = False, 
                           plot_picks: bool = False) -> None:
    out_root = Path(download_cfg.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    stations_xml_dir = out_root / "stations_xml"
    stations_xml_dir.mkdir(parents=True, exist_ok=True)
    client = build_client(download_cfg)

    origin = ensure_utc(event.origin_time_iso)
    if origin is None: raise ValueError("origin_time_iso obbligatorio")

    starttime = origin - download_cfg.t_before_origin
    endtime = origin + download_cfg.t_after_origin

    for sta in stations:
        tag = station_tag(sta.network, sta.station, sta.location, sta.channel_prefix)
        ai_entry = ai_picks.get((sta.network, sta.station, sta.channel_prefix), None)

        if len(ai_picks) > 0 and ai_entry is None:
            print(f"[SKIP] {tag}: Saltata. Non è presente nel file JSON dell'AI.")
            continue

        filter_def = parse_filter_arg(filter_arg, ai_entry)

        try:
            # 1. Carichiamo l'XML della stazione
            inv, xml_path, loaded_from_cache = get_or_load_stationxml(client=client, station_req=sta,
                                                                      cache_dir=stations_xml_dir)
            # 2. Estraiamo le coordinate
            sta_lat, sta_lon, _ = get_station_coordinates(inv)
        except Exception as exc:
            continue

        # 3. Calcoliamo la distanza e formattiamo il prefisso
        dist_deg = locations2degrees(event.latitude, event.longitude, sta_lat, sta_lon)
        dist_km = degrees2kilometers(dist_deg)
        dist_prefix = f"{int(round(dist_km)):04d}_"

        # 4. Creiamo la directory con il prefisso (es. 0234_IV.SNTG.--.HH)
        sta_dir = out_root / f"{dist_prefix}{tag}"

        p_pick, s_pick = resolve_reference_picks(event, inv, tt_cfg, cake_model, ai_entry)

        try:
            st = download_station_stream(client, sta, starttime, endtime)
        except Exception as exc:
            continue

        if len(st) == 0: continue

        if make_full:
            save_per_channel_mseed(st, sta_dir, event, "full")
            full_base = sta_dir / f"{tag}_full"
            saved_files = plot_full_station(st, event, sta, plot_cfg, full_base, p_pick=p_pick, s_pick=s_pick,
                                            plot_picks=plot_picks, filter_def=filter_def)
            full_window_start = 0.0
            full_window_end = float(last_sample_time_for_stream(st) - first_sample_time_for_stream(st))
            write_plot_metadata(basepath_no_ext=full_base, saved_files=saved_files, station_req=sta, event=event,
                                stream=st, plot_cfg=plot_cfg, plot_type="full", tick_major_s=plot_cfg.full_major_tick_s,
                                tick_minor_s=plot_cfg.full_minor_tick_s, window_start_relative_s=full_window_start,
                                window_end_relative_s=full_window_end, plot_picks=plot_picks, filter_def=filter_def,
                                zoom_level=None, zoom_reference_phase=None, zoom_reference_time=None)

        if make_zoom and p_pick is not None:
            p_rel = relative_seconds_from_first_sample(st, p_pick)
            for level_name, half_width_s, major_tick_s, minor_tick_s in iter_requested_zoom_specs(zoom_levels, download_cfg, plot_cfg, zoom_level_presets):
                
                win_pre, win_post = get_asymmetric_windows("P", half_width_s, ai_entry, p_pick, s_pick)
                
                st_cut_p = st.slice(p_pick - win_pre, p_pick + win_post)
                if len(st_cut_p) > 0:
                    save_per_channel_mseed(st_cut_p, sta_dir, event, f"zoom_P_{level_name}")
                
                p_base = sta_dir / f"{tag}_zoom_P" if level_name == "single" else sta_dir / f"{tag}_zoom_P_{level_name}"
                saved_files = plot_zoom_around_pick(st, sta, p_pick, "P", plot_cfg=plot_cfg,
                                                    win_pre_s=win_pre, win_post_s=win_post, out_basepath_no_ext=p_base,
                                                    plot_picks=plot_picks, zoom_major_tick_s=major_tick_s,
                                                    zoom_minor_tick_s=minor_tick_s, filter_def=filter_def)
                write_plot_metadata(basepath_no_ext=p_base, saved_files=saved_files, station_req=sta, event=event,
                                    stream=st, plot_cfg=plot_cfg, plot_type="zoom", tick_major_s=major_tick_s,
                                    tick_minor_s=minor_tick_s,
                                    window_start_relative_s=p_rel - win_pre,
                                    window_end_relative_s=p_rel + win_post, plot_picks=plot_picks,
                                    filter_def=filter_def,
                                    zoom_level=level_name, zoom_reference_phase="P", zoom_reference_time=p_pick)

        if make_zoom and s_pick is not None:
            s_rel = relative_seconds_from_first_sample(st, s_pick)
            for level_name, half_width_s, major_tick_s, minor_tick_s in iter_requested_zoom_specs(zoom_levels, download_cfg, plot_cfg, zoom_level_presets):
                
                win_pre, win_post = get_asymmetric_windows("S", half_width_s, ai_entry, p_pick, s_pick)
                
                st_cut_s = st.slice(s_pick - win_pre, s_pick + win_post)
                if len(st_cut_s) > 0:
                    save_per_channel_mseed(st_cut_s, sta_dir, event, f"zoom_S_{level_name}")
                
                s_base = sta_dir / f"{tag}_zoom_S" if level_name == "single" else sta_dir / f"{tag}_zoom_S_{level_name}"
                saved_files = plot_zoom_around_pick(st, sta, s_pick, "S", plot_cfg=plot_cfg,
                                                    win_pre_s=win_pre, win_post_s=win_post, out_basepath_no_ext=s_base,
                                                    plot_picks=plot_picks, zoom_major_tick_s=major_tick_s,
                                                    zoom_minor_tick_s=minor_tick_s, filter_def=filter_def)
                write_plot_metadata(basepath_no_ext=s_base, saved_files=saved_files, station_req=sta, event=event,
                                    stream=st, plot_cfg=plot_cfg, plot_type="zoom", tick_major_s=major_tick_s,
                                    tick_minor_s=minor_tick_s,
                                    window_start_relative_s=s_rel - win_pre,
                                    window_end_relative_s=s_rel + win_post, plot_picks=plot_picks,
                                    filter_def=filter_def,
                                    zoom_level=level_name, zoom_reference_phase="S", zoom_reference_time=s_pick)

        print(f"[OK] Elaborata stazione {tag}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.full and not args.zoom:
        args.full = True

    if args.write_default_config:
        out = Path(args.write_default_config)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f: json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"[OK] Scritto file di configurazione di esempio: {out}")
        return

    if not args.event and not args.eventid:
        parser.error("Devi fornire --event (stringa manuale) oppure --eventid (per scaricare da FDSN)")

    cfg_dict = load_json_config(args.config)
    zoom_level_presets = load_zoom_level_presets(cfg_dict)
    download_cfg, plot_cfg, tt_cfg = build_configs(cfg_dict)
    zoom_levels = parse_zoom_levels_arg(args.zoom_levels)
    ai_picks = load_ai_picks_json(args.ai_picks_json)

    # --- APPLICAZIONE OVERRIDE DA CLI PER EXPAND DYNAMICS ---
    plot_cfg.expand_dynamics_enabled = args.expand_dynamics
    if args.expand_window is not None:
        plot_cfg.expand_window_s = args.expand_window

    main_client = build_client(download_cfg)

    if args.eventid:
        event = fetch_event_info_from_fdsn(main_client, args.eventid, args.originid)
        download_cfg.output_dir = f"waveforms_event_eid{event.event_id}_oid{event.origin_id}"
    else:
        event = parse_event_arg(args.event)
        download_cfg.output_dir = "waveforms_event_manual"

    event.pick_p_iso = args.pick_p
    event.pick_s_iso = args.pick_s

    if args.networks and args.distances:
        if args.stations:
            print("[WARN] Attenzione: hai specificato sia --stations che --networks/--distances.")
            print("[WARN] La lista manuale --stations verrà IGNORATA. Uso la scoperta geografica FDSN.")

        stations = fetch_stations_by_distance(
            client=main_client,
            event=event,
            networks=args.networks,
            distances_str=args.distances,
            channels=args.channels
        )
    elif args.stations:
        stations = parse_stations_arg(args.stations)
    else:
        parser.error("Devi fornire --stations OPPURE i parametri --networks e --distances per la ricerca.")

    if not stations:
        print("[ERRORE] Nessuna stazione da elaborare. Uscita.")
        return

    cake_model = None
    if tt_cfg.enabled:
        cake_model = load_cake_model_safe(tt_cfg.model_name)

    process_event_stations(
        event=event,
        stations=stations,
        download_cfg=download_cfg,
        plot_cfg=plot_cfg,
        tt_cfg=tt_cfg,
        cake_model=cake_model,
        ai_picks=ai_picks,
        zoom_levels=zoom_levels,
        zoom_level_presets=zoom_level_presets,
        filter_arg=args.filter,
        make_full=args.full,
        make_zoom=args.zoom,
        plot_picks=args.plot_picks,
    )


if __name__ == "__main__":
    main()
