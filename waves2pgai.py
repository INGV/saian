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


def stationxml_filename(net: str, sta: str, loc: str, ch_prefix: str) -> str:
    return f"{net}.{sta}.{safe_loc(loc)}.{ch_prefix}.stationxml"


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


def build_configs(cfg_dict: dict) -> tuple[DownloadConfig, PlotConfig]:
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
    return dcfg, pcfg


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
    if p_pick is not None:
        xp = pick_relative_seconds(tr, p_pick)
        ax.axvline(xp, color="tab:red", lw=line_width, ls="--", alpha=0.95)
        ax.text(
            xp, 0.97, "P",
            color="tab:red",
            transform=ax.get_xaxis_transform(),
            ha="left", va="top", fontsize=9, fontweight="bold"
        )

    if s_pick is not None:
        xs = pick_relative_seconds(tr, s_pick)
        ax.axvline(xs, color="tab:blue", lw=line_width, ls="--", alpha=0.95)
        ax.text(
            xs, 0.97, "S",
            color="tab:blue",
            transform=ax.get_xaxis_transform(),
            ha="left", va="top", fontsize=9, fontweight="bold"
        )


def save_figure_multi_format(fig, basepath_no_ext: Path, formats: Iterable[str], dpi: int) -> None:
    for fmt in formats:
        out_file = basepath_no_ext.parent / f"{basepath_no_ext.name}.{fmt}"
        if fmt.lower() == "png":
            fig.savefig(out_file, dpi=dpi, bbox_inches="tight")
        else:
            fig.savefig(out_file, bbox_inches="tight")


def plot_full_station(
    stream: Stream,
    event: EventInfo,
    station_req: StationRequest,
    plot_cfg: PlotConfig,
    out_basepath_no_ext: Path,
) -> None:
    st = group_3c_for_plot(stream)
    if len(st) == 0:
        return

    p_pick = ensure_utc(event.pick_p_iso)
    s_pick = ensure_utc(event.pick_s_iso)

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

        add_pick_lines(
            ax, tr, p_pick, s_pick,
            line_width=plot_cfg.pick_line_width
        )

        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis="y", which="major", labelsize=8)
        ax.tick_params(axis="y", which="minor", length=2)

    axes[-1].set_xlabel("Tempo relativo al primo campione [s]", fontsize=11)

    origin = ensure_utc(event.origin_time_iso)
    title = (
        f"{station_req.network}.{station_req.station}.{safe_loc(station_req.location)}.{station_req.channel_prefix}   "
        f"Origine: {origin.isoformat()}   "
        f"Lat={event.latitude:.5f} Lon={event.longitude:.5f} Depth={event.depth_km:.2f} km"
    )
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    save_figure_multi_format(fig, out_basepath_no_ext, plot_cfg.formats, plot_cfg.dpi)
    plt.close(fig)


def plot_zoom_around_pick(
    stream: Stream,
    station_req: StationRequest,
    pick_time: UTCDateTime,
    pick_label: str,
    plot_cfg: PlotConfig,
    zoom_half_width_s: float,
    out_basepath_no_ext: Path,
) -> None:
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
            major_tick_s=plot_cfg.zoom_major_tick_s,
            minor_tick_s=plot_cfg.zoom_minor_tick_s,
            draw_major_grid=plot_cfg.draw_major_grid,
            draw_minor_grid=plot_cfg.draw_minor_grid,
        )

        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis="y", which="major", labelsize=8)
        ax.tick_params(axis="y", which="minor", length=2)

    axes[-1].set_xlabel("Tempo relativo al primo campione [s]", fontsize=11)
    title = (
        f"{station_req.network}.{station_req.station}.{safe_loc(station_req.location)}.{station_req.channel_prefix}   "
        f"Zoom {pick_label.upper()} @ {pick_time.isoformat()}"
    )
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    save_figure_multi_format(fig, out_basepath_no_ext, plot_cfg.formats, plot_cfg.dpi)
    plt.close(fig)


# ============================================================
# WORKFLOW
# ============================================================

def process_event_stations(
    event: EventInfo,
    stations: Iterable[StationRequest],
    download_cfg: DownloadConfig,
    plot_cfg: PlotConfig,
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

    p_pick = ensure_utc(event.pick_p_iso)
    s_pick = ensure_utc(event.pick_s_iso)

    for sta in stations:
        tag = station_tag(sta.network, sta.station, sta.location, sta.channel_prefix)
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

        full_base = sta_dir / f"{tag}_full"
        plot_full_station(st, event, sta, plot_cfg, full_base)

        if p_pick is not None:
            p_base = sta_dir / f"{tag}_zoom_P"
            plot_zoom_around_pick(
                st, sta, p_pick, "P",
                plot_cfg=plot_cfg,
                zoom_half_width_s=download_cfg.zoom_half_width_s,
                out_basepath_no_ext=p_base,
            )

        if s_pick is not None:
            s_base = sta_dir / f"{tag}_zoom_S"
            plot_zoom_around_pick(
                st, sta, s_pick, "S",
                plot_cfg=plot_cfg,
                zoom_half_width_s=download_cfg.zoom_half_width_s,
                out_basepath_no_ext=s_base,
            )

        print(f"[OK] Elaborata stazione {tag}")
        print("DEBUG full_base =", full_base)
        
        if p_pick is not None:
            p_base = sta_dir / f"{tag}_zoom_P"
            print("DEBUG p_base =", p_base)
        
        if s_pick is not None:
            s_base = sta_dir / f"{tag}_zoom_S"
            print("DEBUG s_base =", s_base)


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

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
    download_cfg, plot_cfg = build_configs(cfg_dict)

    event = parse_event_arg(args.event)
    event.pick_p_iso = args.pick_p
    event.pick_s_iso = args.pick_s

    stations = parse_stations_arg(args.stations)

    process_event_stations(
        event=event,
        stations=stations,
        download_cfg=download_cfg,
        plot_cfg=plot_cfg,
    )


if __name__ == "__main__":
    main()
