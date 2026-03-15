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
from obspy.geodetics import locations2degrees
from pyrocko import cake


# ============================================================
# DATACLASS
# ============================================================

@dataclass
class StationRequest:
    network: str
    station: str
    channel_prefix: str   # es. HH, HN, BH
    location: str = "*"

    @property
    def waveform_channel_pattern(self) -> str:
        # HH -> HH? ; HNZ -> HNZ ; HH* -> HH*
        cp = self.channel_prefix.strip()
        if "*" in cp or "?" in cp:
            return cp
        if len(cp) == 2:
            return f"{cp}?"
        return cp

    @property
    def stationxml_channel_pattern(self) -> str:
        # per metadata: tutti i canali che iniziano con il prefisso
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
    formats: tuple[str, ...] = ("png", "pdf")
    line_width: float = 0.7

    # full plot
    full_major_tick_s: float = 0.1
    full_minor_tick_s: float = 0.01

    # zoom plot
    zoom_major_tick_s: float = 0.1
    zoom_minor_tick_s: float = 0.01

    # stile
    draw_minor_grid: bool = True
    draw_major_grid: bool = True
    pick_line_width: float = 1.2

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

# ============================================================
# DEFAULT CONFIG JSON-STYLE
# ============================================================

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
        "formats": ["png", "pdf"],
        "line_width": 0.7,
        "full_major_tick_s": 0.1,
        "full_minor_tick_s": 0.01,
        "zoom_major_tick_s": 0.1,
        "zoom_minor_tick_s": 0.01,
        "draw_minor_grid": True,
        "draw_major_grid": True,
        "pick_line_width": 1.2,
    },
    "travel_time": {
        "enabled": True,
        "model_name": "ak135-f-continental.m",
        "receiver_depth_km": 0.0
    },
    "zoom_levels": {
        "context": {
            "half_width_s": 3.0,
            "major_tick_s": 0.5,
            "minor_tick_s": 0.1
        },
        "fine": {
            "half_width_s": 1.0,
            "major_tick_s": 0.1,
            "minor_tick_s": 0.02
        },
        "ultrafine": {
            "half_width_s": 0.4,
            "major_tick_s": 0.1,
            "minor_tick_s": 0.02
        }
    }
}


# ============================================================
# UTIL
# ============================================================

def ensure_utc(value: str | UTCDateTime | None) -> Optional[UTCDateTime]:
    if value is None:
        return None
    if isinstance(value, UTCDateTime):
        return value
    return UTCDateTime(value)


def safe_loc(loc: str) -> str:
    return loc if loc and loc != "*" else "--"


def station_tag(network: str, station: str, location: str, channel_prefix: str) -> str:
    return f"{network}.{station}.{safe_loc(location)}.{channel_prefix}"


def channel_filename(trace: Trace) -> str:
    loc = safe_loc(trace.stats.location)
    return (
        f"{trace.stats.network}.{trace.stats.station}.{loc}."
        f"{trace.stats.channel}.{trace.stats.starttime.strftime('%Y%m%dT%H%M%S')}.mseed"
    )


def stationxml_cache_filename(net: str, sta: str, loc: str) -> str:
    return f"{net}.{sta}.{safe_loc(loc)}.xml"


def get_relative_time_axis(trace: Trace) -> np.ndarray:
    return np.arange(trace.stats.npts, dtype=float) * trace.stats.delta


def pick_relative_seconds(trace: Trace, pick_time: UTCDateTime) -> float:
    return float(pick_time - trace.stats.starttime)


def preprocess_trace_for_plot(trace: Trace) -> Trace:
    tr = trace.copy()
    tr.detrend("demean")
    tr.detrend("linear")
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
    """
    Carica il JSON restituito dalla AI e costruisce una mappa
    (network, station, channel_prefix) -> dict pick
    """
    if path is None:
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = {}

    for item in data.get("stations", []):
        net = item["network"].strip()
        sta = item["stacode"].strip()
        cha = item["channel_code"].strip()

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
        user=d.get("user"),
        password=d.get("password"),
    )

    figsize = p.get("figsize", [20, 10])
    pcfg = PlotConfig(
        figsize=(float(figsize[0]), float(figsize[1])),
        dpi=int(p.get("dpi", 800)),
        formats=tuple(str(x).lower() for x in p.get("formats", ["png", "pdf"])),
        line_width=float(p.get("line_width", 0.7)),
        full_major_tick_s=float(p.get("full_major_tick_s", 0.1)),
        full_minor_tick_s=float(p.get("full_minor_tick_s", 0.01)),
        zoom_major_tick_s=float(p.get("zoom_major_tick_s", 0.1)),
        zoom_minor_tick_s=float(p.get("zoom_minor_tick_s", 0.01)),
        draw_minor_grid=bool(p.get("draw_minor_grid", True)),
        draw_major_grid=bool(p.get("draw_major_grid", True)),
        pick_line_width=float(p.get("pick_line_width", 1.2)),
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


def parse_zoom_levels_arg(value: Optional[str]) -> list[str]:
    """
    Esempi validi:
      None -> ["single"]
      "single"
      "context,fine"
      "all"
    """
    if value is None or not value.strip():
        return ["single"]

    tokens = [x.strip().lower() for x in value.split(",") if x.strip()]

    if "all" in tokens:
        tokens = ["context", "fine", "ultrafine"]

    valid = {"single", "context", "fine", "ultrafine"}
    invalid = [x for x in tokens if x not in valid]
    if invalid:
        raise ValueError(
            f"Livelli zoom non validi: {invalid}. "
            f"Valori ammessi: {sorted(valid)} oppure 'all'"
        )

    # dedup preservando l'ordine
    out: list[str] = []
    for token in tokens:
        if token not in out:
            out.append(token)

    return out


def iter_requested_zoom_specs(
    zoom_levels: list[str],
    download_cfg: DownloadConfig,
    plot_cfg: PlotConfig,
    zoom_level_presets: dict[str, ZoomLevelPreset],
) -> list[tuple[str, float, float, float]]:
    """
    Restituisce tuple:
      (level_name, half_width_s, major_tick_s, minor_tick_s)
    """
    out = []

    for level_name in zoom_levels:
        if level_name == "single":
            out.append((
                "single",
                float(download_cfg.zoom_half_width_s),
                float(plot_cfg.zoom_major_tick_s),
                float(plot_cfg.zoom_minor_tick_s),
            ))
        else:
            preset = zoom_level_presets[level_name]
            out.append((
                level_name,
                float(preset.half_width_s),
                float(preset.major_tick_s),
                float(preset.minor_tick_s),
            ))

    return out


# ============================================================
# PARSING CLI
# ============================================================

def parse_event_arg(event_arg: str) -> EventInfo:
    parts = [x.strip() for x in event_arg.split(",")]
    if len(parts) != 4:
        raise ValueError(
            "--event deve essere nel formato "
            '"OT,LAT,LON,DEP" ad es. '
            '"2026-03-01T12:34:56.789000Z,42.12345,13.54321,11.2"'
        )

    ot, lat, lon, dep = parts
    return EventInfo(
        origin_time_iso=ot,
        latitude=float(lat),
        longitude=float(lon),
        depth_km=float(dep),
    )


def parse_stations_arg(stations_arg: str) -> list[StationRequest]:
    """
    Formati supportati:
      NET.STA.CH
      NET.STA.LOC.CH
    Esempi:
      IV.AQU.HH
      IV.AQU.--.HH
      IV.MTRA.BH
    """
    result: list[StationRequest] = []

    for token in stations_arg.split(","):
        token = token.strip()
        if not token:
            continue

        parts = token.split(".")
        if len(parts) == 3:
            net, sta, ch = parts
            loc = "*"
        elif len(parts) == 4:
            net, sta, loc, ch = parts
            if loc == "--":
                loc = ""
        else:
            raise ValueError(
                f"Stazione non valida: '{token}'. "
                "Usa NET.STA.CH oppure NET.STA.LOC.CH"
            )

        result.append(
            StationRequest(
                network=net,
                station=sta,
                location=loc if loc != "" else "",
                channel_prefix=ch,
            )
        )

    if not result:
        raise ValueError("Nessuna stazione valida in --stations")
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scarica waveform e StationXML da INGV FDSN per un evento sismico, "
            "salva miniSEED per canale e figure full/zoom."
        )
    )

    parser.add_argument(
        "--event",
        required=False,
        help='Evento nel formato "OT,LAT,LON,DEP"',
    )
    parser.add_argument(
        "--stations",
        required=False,
        help='Lista stazioni nel formato "NET.STA.CH,NET.STA.CH" '
             'oppure "NET.STA.LOC.CH,NET.STA.LOC.CH"',
    )
    parser.add_argument(
        "--pick-p",
        default=None,
        help="Tempo assoluto pick P in ISO completo",
    )
    parser.add_argument(
        "--pick-s",
        default=None,
        help="Tempo assoluto pick S in ISO completo",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Percorso file JSON di configurazione",
    )
    parser.add_argument(
        "--write-default-config",
        default=None,
        help="Scrive un file JSON di esempio e termina",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="waves2pgai 0.1"
    )
    parser.add_argument(
        "--zoom",
        action="store_true",
        help="Genera gli zoom intorno ai pick P e/o S, se disponibili",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Genera il plot completo della finestra waveform",
    )
    parser.add_argument(
        "--plot-picks",
        action="store_true",
        help="Disegna i pick P/S sui plot, se disponibili",
    )

    parser.add_argument(
        "--ai-picks-json",
        default=None,
        help="File JSON con i pick restituiti dalla AI generativa"
    )

    parser.add_argument(
        "--zoom-levels",
        default=None,
        help=(
            "Livelli di zoom separati da virgola. "
            "Valori ammessi: single,context,fine,ultrafine,all. "
            "Se omesso e usi --zoom, viene usato 'single' (comportamento legacy)."
        ),
    )
    return parser


# ============================================================
# CLIENT
# ============================================================

def build_client(cfg: DownloadConfig) -> Client:
    if cfg.user and cfg.password:
        return Client(base_url=cfg.base_url, user=cfg.user, password=cfg.password)
    return Client(base_url=cfg.base_url)


# ============================================================
# DOWNLOAD WAVEFORMS
# ============================================================

def download_station_stream(
    client: Client,
    station_req: StationRequest,
    starttime: UTCDateTime,
    endtime: UTCDateTime,
) -> Stream:
    location = station_req.location if station_req.location != "" else "*"
    channel = station_req.waveform_channel_pattern

    base = "https://webservices.ingv.it/fdsnws/dataselect/1/query"
    query_url = (
        f"{base}"
        f"?network={station_req.network}"
        f"&station={station_req.station}"
        f"&location={location}"
        f"&channel={channel}"
        f"&starttime={starttime.isoformat()}"
        f"&endtime={endtime.isoformat()}"
    )

    print("[DATASELECT QUERY PARAMS]")
    print(f"  network   = {station_req.network}")
    print(f"  station   = {station_req.station}")
    print(f"  location  = {location}")
    print(f"  channel   = {channel}")
    print(f"  starttime = {starttime.isoformat()}")
    print(f"  endtime   = {endtime.isoformat()}")
    print("[DATASELECT QUERY URL]")
    print(query_url)

    st = client.get_waveforms(
        network=station_req.network,
        station=station_req.station,
        location=location,
        channel=channel,
        starttime=starttime,
        endtime=endtime,
        attach_response=False,
    )
    st.trim(starttime=starttime, endtime=endtime, nearest_sample=False)
    st.merge(method=1, fill_value="interpolate")
    st.sort()
    return st

# ============================================================
# DOWNLOAD / CACHE STATIONXML
# ============================================================

def download_stationxml_full(
    client: Client,
    station_req: StationRequest,
) -> Inventory:
    inv = client.get_stations(
        network=station_req.network,
        station=station_req.station,
        location=station_req.location if station_req.location != "" else "*",
        level="response",
        format="xml",
    )
    return inv


def save_stationxml(inv: Inventory, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    inv.write(str(out_file), format="STATIONXML")


def get_or_load_stationxml(
    client: Client,
    station_req: StationRequest,
    cache_dir: Path,
) -> tuple[Inventory, Path, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)

    xml_path = cache_dir / stationxml_cache_filename(
        station_req.network,
        station_req.station,
        station_req.location,
    )

    if xml_path.exists():
        inv = read_inventory(str(xml_path))
        return inv, xml_path, True

    inv = download_stationxml_full(client, station_req)
    save_stationxml(inv, xml_path)
    return inv, xml_path, False

def get_station_coordinates(inv: Inventory) -> tuple[float, float, float]:
    """
    Restituisce lat, lon, elev_m dalla prima stazione trovata nell'Inventory.
    """
    if len(inv.networks) == 0 or len(inv.networks[0].stations) == 0:
        raise ValueError("Inventory senza stazioni")

    sta = inv.networks[0].stations[0]
    return float(sta.latitude), float(sta.longitude), float(sta.elevation)


def load_cake_model_safe(model_name: str):
    """
    Carica il modello Cake richiesto, con fallback al default Pyrocko.
    """
    try:
        model = cake.load_model(model_name)
        print(f"[OK] Cake model loaded: {model_name}")
        return model
    except Exception as exc:
        print(
            f"[WARN] impossibile caricare il modello Cake richiesto "
            f"'{model_name}': {exc}. Uso il modello di default di Pyrocko."
        )
        return cake.load_model()


def theoretical_phase_pick(
    model,
    origin: UTCDateTime,
    event_lat: float,
    event_lon: float,
    event_depth_km: float,
    station_lat: float,
    station_lon: float,
    phase_name: str,
    tt_cfg: TravelTimeConfig,
) -> Optional[UTCDateTime]:
    """
    Calcola il pick teorico assoluto per una fase, usando una lista di
    candidati e scegliendo il primo arrivo disponibile.
    """

    dist_deg = locations2degrees(
        lat1=event_lat,
        long1=event_lon,
        lat2=station_lat,
        long2=station_lon,
    )

    # Fasi da provare: prima quelle locali/crostali, poi fallback più generici
    if phase_name.upper() == "P":
        candidates = ["Pg", "p", "P"]
    elif phase_name.upper() == "S":
        candidates = ["Sg", "s", "S"]
    else:
        candidates = [phase_name]

    best_ray = None
    best_name = None

    for cname in candidates:
        try:
            phases = cake.PhaseDef.classic(cname)
            rays = model.arrivals(
                phases=phases,
                distances=[dist_deg],
                zstart=event_depth_km * 1000.0,
                zstop=tt_cfg.receiver_depth_km * 1000.0,
            )
        except Exception as exc:
            print(f"[WARN] Cake fallito per fase {cname}: {exc}")
            continue

        if not rays:
            print(f"[DEBUG] Nessun arrivo per fase teorica {cname} a {dist_deg:.3f} deg")
            continue

        first = sorted(rays, key=lambda r: r.t)[0]

        if best_ray is None or first.t < best_ray.t:
            best_ray = first
            best_name = cname

    if best_ray is None:
        print(
            f"[WARN] Nessun pick teorico trovato per {phase_name} "
            f"(candidate={candidates}, dist={dist_deg:.3f} deg, depth={event_depth_km:.1f} km)"
        )
        return None

    abs_pick = origin + float(best_ray.t)
    print(
        f"[INFO] pick teorico {phase_name} = {abs_pick.isoformat()} "
        f"usando fase {best_name} a distanza {dist_deg:.3f} deg"
    )
    return abs_pick

# ============================================================
# SALVATAGGIO MINISED
# ============================================================

def save_per_channel_mseed(stream: Stream, station_out_dir: Path) -> None:
    station_out_dir.mkdir(parents=True, exist_ok=True)
    for tr in stream:
        out_file = station_out_dir / channel_filename(tr)
        tr.write(str(out_file), format="MSEED")


# ============================================================
# PLOTTING
# ============================================================

def style_time_axis(
    ax,
    major_tick_s: float,
    minor_tick_s: float,
    draw_major_grid: bool,
    draw_minor_grid: bool,
) -> None:
    ax.xaxis.set_major_locator(MultipleLocator(major_tick_s))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.3f"))
    ax.xaxis.set_minor_locator(MultipleLocator(minor_tick_s))

    ax.tick_params(axis="x", which="major", length=8, width=0.8, labelsize=9)
    ax.tick_params(axis="x", which="minor", length=4, width=0.6, labelbottom=False)

    ax.grid(False, which="major", axis="x")
    ax.grid(False, which="minor", axis="x")

    if draw_major_grid:
        ax.grid(True, which="major", axis="x", alpha=0.30)

    if draw_minor_grid:
        ax.grid(True, which="minor", axis="x", alpha=0.12)


def add_pick_lines(
    ax,
    tr: Trace,
    p_pick: Optional[UTCDateTime],
    s_pick: Optional[UTCDateTime],
    line_width: float,
) -> None:

    print(f"DEBUG trace starttime = {tr.stats.starttime}")
    if p_pick is not None:
        print(f"DEBUG add_pick_lines P = {p_pick}")
        xp = pick_relative_seconds(tr, p_pick)
        ax.axvline(xp, color="tab:red", lw=line_width, ls="--", alpha=0.95)
        ax.text(
            xp, 0.97, "P",
            color="tab:red",
            transform=ax.get_xaxis_transform(),
            ha="left", va="top", fontsize=9, fontweight="bold"
        )

    if s_pick is not None:
        print(f"DEBUG add_pick_lines S = {s_pick}")
        xs = pick_relative_seconds(tr, s_pick)
        ax.axvline(xs, color="tab:blue", lw=line_width, ls="--", alpha=0.95)
        ax.text(
            xs, 0.97, "S",
            color="tab:blue",
            transform=ax.get_xaxis_transform(),
            ha="left", va="top", fontsize=9, fontweight="bold"
        )


def save_figure_multi_format(fig, basepath_no_ext: Path, formats: Iterable[str], dpi: int) -> list[Path]:
    saved_files: list[Path] = []

    for fmt in formats:
        out_file = basepath_no_ext.parent / f"{basepath_no_ext.name}.{fmt}"
        if fmt.lower() == "png":
            fig.savefig(out_file, dpi=dpi, bbox_inches="tight")
        else:
            fig.savefig(out_file, bbox_inches="tight")

        saved_files.append(out_file)
    return saved_files

# Helpers for metadata json
def first_sample_time_for_stream(stream: Stream) -> UTCDateTime:
    st = group_3c_for_plot(stream)
    if len(st) == 0:
        raise ValueError("Stream vuoto")
    return min(tr.stats.starttime for tr in st)


def last_sample_time_for_stream(stream: Stream) -> UTCDateTime:
    st = group_3c_for_plot(stream)
    if len(st) == 0:
        raise ValueError("Stream vuoto")
    return max(tr.stats.endtime for tr in st)


def relative_seconds_from_first_sample(stream: Stream, abs_time: UTCDateTime) -> float:
    return float(abs_time - first_sample_time_for_stream(stream))


def trace_descriptors_for_metadata(stream: Stream) -> list[dict]:
    st = group_3c_for_plot(stream)
    out = []

    for tr in st:
        out.append(
            {
                "id": tr.id,
                "channel": tr.stats.channel,
                "starttime": tr.stats.starttime.isoformat(),
                "endtime": tr.stats.endtime.isoformat(),
                "npts": int(tr.stats.npts),
                "sample_rate_hz": float(tr.stats.sampling_rate),
                "delta_s": float(tr.stats.delta),
            }
        )

    return out


def write_plot_metadata(
    basepath_no_ext: Path,
    saved_files: list[Path],
    station_req: StationRequest,
    event: EventInfo,
    stream: Stream,
    plot_type: str,
    tick_major_s: float,
    tick_minor_s: float,
    window_start_relative_s: float,
    window_end_relative_s: float,
    plot_picks: bool,
    zoom_level: Optional[str] = None,
    zoom_reference_phase: Optional[str] = None,
    zoom_reference_time: Optional[UTCDateTime] = None,
) -> None:
    st = group_3c_for_plot(stream)
    if len(st) == 0:
        return

    first_sample = first_sample_time_for_stream(st)
    last_sample = last_sample_time_for_stream(st)
    origin = ensure_utc(event.origin_time_iso)

    metadata = {
        "files": [p.name for p in saved_files],
        "station": {
            "network": station_req.network,
            "stacode": station_req.station,
            "location": safe_loc(station_req.location),
            "channel_code": station_req.channel_prefix,
        },
        "plot": {
            "plot_type": plot_type,
            "zoom_level": zoom_level,
            "x_axis_reference": "relative_to_first_sample",
            "first_sample_time": first_sample.isoformat(),
            "last_sample_time": last_sample.isoformat(),
            "origin_time": origin.isoformat() if origin is not None else None,
            "window_start_relative_s": float(window_start_relative_s),
            "window_end_relative_s": float(window_end_relative_s),
            "duration_s": float(window_end_relative_s - window_start_relative_s),
            "zoom_reference_phase": zoom_reference_phase,
            "zoom_reference_time": zoom_reference_time.isoformat() if zoom_reference_time is not None else None,
            "tick_major_s": float(tick_major_s),
            "tick_minor_s": float(tick_minor_s),
            "plot_picks": bool(plot_picks),
        },
        "sampling": {
            "sample_rate_hz": float(st[0].stats.sampling_rate),
            "delta_s": float(st[0].stats.delta),
        },
        "channels": [tr.stats.channel for tr in st],
        "traces": trace_descriptors_for_metadata(st),
        "processing": {
            "filtered": False,
            "filter": None,
            "vertical_exaggeration": {
                "enabled": False,
                "channels": [],
                "factor": 1.0,
            },
        },
    }

    meta_path = basepath_no_ext.parent / f"{basepath_no_ext.name}.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"[OK] scritto metadata plot -> {meta_path}")




def plot_full_station(
    stream: Stream,
    event: EventInfo,
    station_req: StationRequest,
    plot_cfg: PlotConfig,
    out_basepath_no_ext: Path,
    p_pick: Optional[UTCDateTime] = None,
    s_pick: Optional[UTCDateTime] = None,
    plot_picks: bool = False,
) -> list[Path]:
    print(f"DEBUG full plot received p_pick = {p_pick}")
    print(f"DEBUG full plot received s_pick = {s_pick}")
    print(f"DEBUG full plot plot_picks = {plot_picks}")
    st = group_3c_for_plot(stream)
    if len(st) == 0:
        return

    fig, axes = plt.subplots(
        len(st), 1,
        figsize=plot_cfg.figsize,
        sharex=False
    )
    if len(st) == 1:
        axes = [axes]

    for ax, tr in zip(axes, st):
        trp = preprocess_trace_for_plot(tr)
        x = get_relative_time_axis(trp)
        y = trp.data.astype(np.float64)

        ax.plot(x, y, color="black", lw=plot_cfg.line_width)
        ax.set_ylabel(tr.stats.channel, rotation=0, labelpad=28, fontsize=10)
        ax.margins(x=0)

        style_time_axis(
            ax,
            major_tick_s=plot_cfg.full_major_tick_s,
            minor_tick_s=plot_cfg.full_minor_tick_s,
            draw_major_grid=plot_cfg.draw_major_grid,
            draw_minor_grid=plot_cfg.draw_minor_grid,
        )

        if plot_picks:
            add_pick_lines(
                ax, tr, p_pick, s_pick,
                line_width=plot_cfg.pick_line_width
            )

        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis="y", which="major", labelsize=8)
        ax.tick_params(axis="y", which="minor", length=2)

    axes[-1].set_xlabel("Tempo relativo al primo campione [s]", fontsize=11)

    origin = ensure_utc(event.origin_time_iso)
    first_sample = first_sample_time_for_stream(st)

    title = (
        f"{station_req.network}.{station_req.station}.{safe_loc(station_req.location)}.{station_req.channel_prefix}\n"
        f"First sample: {first_sample.isoformat()}   Origin: {origin.isoformat()}\n"
        f"Lat={event.latitude:.5f} Lon={event.longitude:.5f} Depth={event.depth_km:.2f} km"
    )

    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    saved_files = save_figure_multi_format(fig, out_basepath_no_ext, plot_cfg.formats, plot_cfg.dpi)
    plt.close(fig)
    return saved_files


def plot_zoom_around_pick(
    stream: Stream,
    station_req: StationRequest,
    pick_time: UTCDateTime,
    pick_label: str,
    plot_cfg: PlotConfig,
    zoom_half_width_s: float,
    out_basepath_no_ext: Path,
    plot_picks: bool = False,
    zoom_major_tick_s: Optional[float] = None,
    zoom_minor_tick_s: Optional[float] = None,
) -> list[Path]:
    st = group_3c_for_plot(stream)
    if len(st) == 0:
        return

    fig, axes = plt.subplots(
        len(st), 1,
        figsize=plot_cfg.figsize,
        sharex=False
    )
    if len(st) == 1:
        axes = [axes]

    for ax, tr in zip(axes, st):
        trp = preprocess_trace_for_plot(tr)
        x = get_relative_time_axis(trp)
        y = trp.data.astype(np.float64)

        x_pick = pick_relative_seconds(tr, pick_time)
        xmin = x_pick - zoom_half_width_s
        xmax = x_pick + zoom_half_width_s

        mask = (x >= xmin) & (x <= xmax)

        if not np.any(mask):
            ax.text(
                0.5, 0.5, "Pick fuori dalla finestra scaricata",
                transform=ax.transAxes,
                ha="center", va="center", fontsize=11
            )
            ax.set_ylabel(tr.stats.channel, rotation=0, labelpad=28, fontsize=10)
            continue

        ax.plot(x[mask], y[mask], color="black", lw=plot_cfg.line_width)

        if plot_picks:
            ax.axvline(
                x_pick,
                color="tab:red" if pick_label.upper() == "P" else "tab:blue",
                lw=plot_cfg.pick_line_width,
                ls="--",
                alpha=0.95
            )
            ax.text(
                x_pick, 0.97, pick_label.upper(),
                color="tab:red" if pick_label.upper() == "P" else "tab:blue",
                transform=ax.get_xaxis_transform(),
                ha="left", va="top", fontsize=9, fontweight="bold"
            )

        ax.set_xlim(xmin, xmax)
        ax.set_ylabel(tr.stats.channel, rotation=0, labelpad=28, fontsize=10)

        style_time_axis(
            ax,
            major_tick_s=zoom_major_tick_s if zoom_major_tick_s is not None else plot_cfg.zoom_major_tick_s,
            minor_tick_s=zoom_minor_tick_s if zoom_minor_tick_s is not None else plot_cfg.zoom_minor_tick_s,
            draw_major_grid=plot_cfg.draw_major_grid,
            draw_minor_grid=plot_cfg.draw_minor_grid,
        )

        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis="y", which="major", labelsize=8)
        ax.tick_params(axis="y", which="minor", length=2)

    axes[-1].set_xlabel("Tempo relativo al primo campione [s]", fontsize=11)
    first_sample = first_sample_time_for_stream(st)

    title = (
        f"{station_req.network}.{station_req.station}.{safe_loc(station_req.location)}.{station_req.channel_prefix}\n"
        f"First sample: {first_sample.isoformat()}\n"
        f"Zoom {pick_label.upper()} reference: {pick_time.isoformat()}"
    )

    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    saved_files = save_figure_multi_format(fig, out_basepath_no_ext, plot_cfg.formats, plot_cfg.dpi)
    plt.close(fig)
    return saved_files

def resolve_reference_picks(
    event: EventInfo,
    inv: Inventory,
    tt_cfg: TravelTimeConfig,
    cake_model,
    ai_entry: Optional[dict] = None
) -> tuple[Optional[UTCDateTime], Optional[UTCDateTime]]:
    """
    Usa i pick osservati (AI o CLI) se forniti; se mancanti, prova a calcolare i teorici.
    AI JSON
    ↓
    CLI --pick-p / --pick-s
    ↓
    theoretical Cake
    """
    origin = ensure_utc(event.origin_time_iso)
    if origin is None:
        raise ValueError("origin_time_iso obbligatorio")

    # priorità 1: AI
    p_pick = None
    s_pick = None

    if ai_entry is not None:
        p_ai = ai_entry.get("pick_p")
        if p_ai and p_ai.get("time"):
            p_pick = ensure_utc(p_ai["time"])
            print(f"[AI] pick P = {p_pick}")

        s_ai = ai_entry.get("pick_s")
        if s_ai and s_ai.get("time"):
            s_pick = ensure_utc(s_ai["time"])
            print(f"[AI] pick S = {s_pick}")

    # priorità 2: CLI
    if p_pick is None:
        p_pick = ensure_utc(event.pick_p_iso)

    if s_pick is None:
        s_pick = ensure_utc(event.pick_s_iso)

    if not tt_cfg.enabled:
        return p_pick, s_pick

    try:
        sta_lat, sta_lon, _ = get_station_coordinates(inv)
    except Exception as exc:
        print(f"[WARN] coordinate stazione non disponibili per traveltime teoriche: {exc}")
        return p_pick, s_pick

    if p_pick is None:
        p_pick = theoretical_phase_pick(
            cake_model,
            origin=origin,
            event_lat=event.latitude,
            event_lon=event.longitude,
            event_depth_km=event.depth_km,
            station_lat=sta_lat,
            station_lon=sta_lon,
            phase_name="P",
            tt_cfg=tt_cfg,
        )
        if p_pick is not None:
            print(f"[INFO] pick P teorico = {p_pick.isoformat()}")

    if s_pick is None:
        s_pick = theoretical_phase_pick(
            cake_model,
            origin=origin,
            event_lat=event.latitude,
            event_lon=event.longitude,
            event_depth_km=event.depth_km,
            station_lat=sta_lat,
            station_lon=sta_lon,
            phase_name="S",
            tt_cfg=tt_cfg,
        )
        if s_pick is not None:
            print(f"[INFO] pick S teorico = {s_pick.isoformat()}")

    return p_pick, s_pick

# ============================================================
# WORKFLOW
# ============================================================

def process_event_stations(
    event: EventInfo,
    stations: Iterable[StationRequest],
    download_cfg: DownloadConfig,
    plot_cfg: PlotConfig,
    tt_cfg: TravelTimeConfig,
    cake_model,
    ai_picks: dict,
    zoom_levels: list[str],
    zoom_level_presets: dict[str, ZoomLevelPreset],
    make_full: bool = True,
    make_zoom: bool = False,
    plot_picks: bool = False,
) -> None:
    out_root = Path(download_cfg.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    stations_xml_dir = out_root / "stations_xml"
    stations_xml_dir.mkdir(parents=True, exist_ok=True)

    client = build_client(download_cfg)

    origin = ensure_utc(event.origin_time_iso)
    if origin is None:
        raise ValueError("origin_time_iso obbligatorio")

    starttime = origin - download_cfg.t_before_origin
    endtime = origin + download_cfg.t_after_origin

    for sta in stations:
        tag = station_tag(sta.network, sta.station, sta.location, sta.channel_prefix)
        ai_entry = ai_picks.get(
            (sta.network, sta.station, sta.channel_prefix),
            None
        )
        sta_dir = out_root / tag

        # 1) StationXML full con cache locale in stations_xml/
        try:
            inv, xml_path, loaded_from_cache = get_or_load_stationxml(
                client=client,
                station_req=sta,
                cache_dir=stations_xml_dir,
            )
            if loaded_from_cache:
                print(f"[OK] {tag}: StationXML letto da cache -> {xml_path}")
            else:
                print(f"[OK] {tag}: StationXML scaricato -> {xml_path}")
        except Exception as exc:
            print(f"[WARN] {tag}: StationXML non disponibile -> {exc}")
            continue

        p_pick, s_pick = resolve_reference_picks(
            event,
            inv,
            tt_cfg,
            cake_model,
            ai_entry
        )

        # 2) Waveforms
        try:
            st = download_station_stream(client, sta, starttime, endtime)
        except Exception as exc:
            print(f"[ERRORE] {tag}: download waveform fallito -> {exc}")
            continue

        if len(st) == 0:
            print(f"[WARN] {tag}: nessun dato waveform scaricato")
            continue

        save_per_channel_mseed(st, sta_dir)
        print(f"DEBUG plot_picks = {plot_picks}")
        print(f"DEBUG resolved p_pick = {p_pick}")
        print(f"DEBUG resolved s_pick = {s_pick}")

        if make_full:
            full_base = sta_dir / f"{tag}_full"
            print(f"DEBUG full_base = {full_base}")

            saved_files = plot_full_station(
                st,
                event,
                sta,
                plot_cfg,
                full_base,
                p_pick=p_pick,
                s_pick=s_pick,
                plot_picks=plot_picks,
            )

            full_window_start = 0.0
            full_window_end = float(last_sample_time_for_stream(st) - first_sample_time_for_stream(st))

            write_plot_metadata(
                basepath_no_ext=full_base,
                saved_files=saved_files,
                station_req=sta,
                event=event,
                stream=st,
                plot_type="full",
                tick_major_s=plot_cfg.full_major_tick_s,
                tick_minor_s=plot_cfg.full_minor_tick_s,
                window_start_relative_s=full_window_start,
                window_end_relative_s=full_window_end,
                plot_picks=plot_picks,
                zoom_level=None,
                zoom_reference_phase=None,
                zoom_reference_time=None,
            )

        if make_zoom and p_pick is not None:
            p_rel = relative_seconds_from_first_sample(st, p_pick)

            for level_name, half_width_s, major_tick_s, minor_tick_s in iter_requested_zoom_specs(
                    zoom_levels,
                    download_cfg,
                    plot_cfg,
                    zoom_level_presets,
            ):
                if level_name == "single":
                    p_base = sta_dir / f"{tag}_zoom_P"
                else:
                    p_base = sta_dir / f"{tag}_zoom_P_{level_name}"

                print(f"DEBUG p_base = {p_base}")

                saved_files = plot_zoom_around_pick(
                    st,
                    sta,
                    p_pick,
                    "P",
                    plot_cfg=plot_cfg,
                    zoom_half_width_s=half_width_s,
                    out_basepath_no_ext=p_base,
                    plot_picks=plot_picks,
                    zoom_major_tick_s=major_tick_s,
                    zoom_minor_tick_s=minor_tick_s,
                )

                write_plot_metadata(
                    basepath_no_ext=p_base,
                    saved_files=saved_files,
                    station_req=sta,
                    event=event,
                    stream=st,
                    plot_type="zoom",
                    tick_major_s=major_tick_s,
                    tick_minor_s=minor_tick_s,
                    window_start_relative_s=p_rel - half_width_s,
                    window_end_relative_s=p_rel + half_width_s,
                    plot_picks=plot_picks,
                    zoom_level=level_name,
                    zoom_reference_phase="P",
                    zoom_reference_time=p_pick,
                )

            p_rel = relative_seconds_from_first_sample(st, p_pick)

            write_plot_metadata(
                basepath_no_ext=p_base,
                saved_files=saved_files,
                station_req=sta,
                event=event,
                stream=st,
                plot_type="zoom",
                tick_major_s=plot_cfg.zoom_major_tick_s,
                tick_minor_s=plot_cfg.zoom_minor_tick_s,
                window_start_relative_s=p_rel - download_cfg.zoom_half_width_s,
                window_end_relative_s=p_rel + download_cfg.zoom_half_width_s,
                plot_picks=plot_picks,
                zoom_level="single",
                zoom_reference_phase="P",
                zoom_reference_time=p_pick,
            )

        if make_zoom and s_pick is not None:
            s_rel = relative_seconds_from_first_sample(st, s_pick)

            for level_name, half_width_s, major_tick_s, minor_tick_s in iter_requested_zoom_specs(
                    zoom_levels,
                    download_cfg,
                    plot_cfg,
                    zoom_level_presets,
            ):
                if level_name == "single":
                    s_base = sta_dir / f"{tag}_zoom_S"
                else:
                    s_base = sta_dir / f"{tag}_zoom_S_{level_name}"

                print(f"DEBUG s_base = {s_base}")

                saved_files = plot_zoom_around_pick(
                    st,
                    sta,
                    s_pick,
                    "S",
                    plot_cfg=plot_cfg,
                    zoom_half_width_s=half_width_s,
                    out_basepath_no_ext=s_base,
                    plot_picks=plot_picks,
                    zoom_major_tick_s=major_tick_s,
                    zoom_minor_tick_s=minor_tick_s,
                )

                write_plot_metadata(
                    basepath_no_ext=s_base,
                    saved_files=saved_files,
                    station_req=sta,
                    event=event,
                    stream=st,
                    plot_type="zoom",
                    tick_major_s=major_tick_s,
                    tick_minor_s=minor_tick_s,
                    window_start_relative_s=s_rel - half_width_s,
                    window_end_relative_s=s_rel + half_width_s,
                    plot_picks=plot_picks,
                    zoom_level=level_name,
                    zoom_reference_phase="S",
                    zoom_reference_time=s_pick,
                )

            s_rel = relative_seconds_from_first_sample(st, s_pick)

            write_plot_metadata(
                basepath_no_ext=s_base,
                saved_files=saved_files,
                station_req=sta,
                event=event,
                stream=st,
                plot_type="zoom",
                tick_major_s=plot_cfg.zoom_major_tick_s,
                tick_minor_s=plot_cfg.zoom_minor_tick_s,
                window_start_relative_s=s_rel - download_cfg.zoom_half_width_s,
                window_end_relative_s=s_rel + download_cfg.zoom_half_width_s,
                plot_picks=plot_picks,
                zoom_level="single",
                zoom_reference_phase="S",
                zoom_reference_time=s_pick,
            )

        print(f"[OK] Elaborata stazione {tag}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    if not args.full and not args.zoom:
        args.full = True
    print(f"[RUN MODE] full={args.full} zoom={args.zoom} plot_picks={args.plot_picks}")
    # modalità scrittura config
    if args.write_default_config:
        out = Path(args.write_default_config)
        out.parent.mkdir(parents=True, exist_ok=True)

        with open(out, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)

        print(f"[OK] Scritto file di configurazione di esempio: {out}")
        return

    # controllo argomenti obbligatori per esecuzione normale
    if not args.event or not args.stations:
        parser.error("--event e --stations sono obbligatori (tranne quando usi --write-default-config)")

    cfg_dict = load_json_config(args.config)
    zoom_level_presets = load_zoom_level_presets(cfg_dict)
    download_cfg, plot_cfg, tt_cfg = build_configs(cfg_dict)

    zoom_levels = parse_zoom_levels_arg(args.zoom_levels)

    print(f"[ZOOM LEVELS] {zoom_levels}")

    ai_picks = load_ai_picks_json(args.ai_picks_json)
    cake_model = None
    if tt_cfg.enabled:
        cake_model = load_cake_model_safe(tt_cfg.model_name)

    event = parse_event_arg(args.event)
    event.pick_p_iso = args.pick_p
    event.pick_s_iso = args.pick_s

    stations = parse_stations_arg(args.stations)

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
        make_full=args.full,
        make_zoom=args.zoom,
        plot_picks=args.plot_picks,
    )


if __name__ == "__main__":
    main()
