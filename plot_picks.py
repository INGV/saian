import argparse
import json
import os
import sys
import glob
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import obspy
from obspy import Stream, read, UTCDateTime

def parse_arguments():
    parser = argparse.ArgumentParser(description="Validazione e Plotting di Forme d'Onda Sismiche.")
    parser.add_argument('--wdir', required=True, help="Directory di lavoro della stazione (es. waveforms_event_.../IV.SNTG.--.HH)")
    parser.add_argument('--stage', required=True, help="Numero dello stage da plottare (es. 1, 2)")
    return parser.parse_args()

def load_json_data(json_path):
    if not os.path.exists(json_path):
        print(f"Errore: Il file JSON '{json_path}' non esiste.")
        sys.exit(1)
    with open(json_path, 'r') as f:
        return json.load(f)

def time_formatter(x, pos):
    """Formatta il tempo assoluto in MM:SS.d (decimi di secondo)"""
    dt = mdates.num2date(x)
    return dt.strftime('%M:%S') + f".{int(dt.microsecond / 100000)}"

def main():
    args = parse_arguments()
    
    # 1) Trova automaticamente il file JSON in base allo stage
    json_pattern = os.path.join(args.wdir, f"*stage{args.stage}*.json")
    json_files = glob.glob(json_pattern)
    
    if not json_files:
        print(f"Errore: Nessun file JSON trovato in '{args.wdir}' per lo stage {args.stage}.")
        sys.exit(1)
    elif len(json_files) > 1:
        print(f"Avviso: Trovati multipli file JSON per lo stage {args.stage}. Uso il primo: {json_files[0]}")
        
    json_path = json_files[0]
    event_data = load_json_data(json_path)
    
    # 2) Trova automaticamente i file MiniSEED 'full'
    mseed_files = glob.glob(os.path.join(args.wdir, "*_full.mseed"))
    
    # Fallback di sicurezza: se non trova _full, prende tutti i mseed scartando gli zoom
    if not mseed_files:
        all_mseed = glob.glob(os.path.join(args.wdir, "*.mseed"))
        mseed_files = [f for f in all_mseed if "zoom" not in f.lower()]
        
    if not mseed_files:
        print(f"Errore: Nessun file MiniSEED 'full' trovato in '{args.wdir}'.")
        sys.exit(1)
        
    # Estrazione metadati attesi dal JSON
    sta_info = event_data['stations'][0]
    exp_net = sta_info['network']
    exp_sta = sta_info['stacode']
    exp_chan_prefix = sta_info['channel_code']
    p_time_str = sta_info['pick_p']['time']
    s_time_str = sta_info['pick_s']['time']
    
    # --- LOG DI CONFERMA E DEBUG ---
    print("\n" + "="*60)
    print("📋 RESOCONTO DATI IN ELABORAZIONE")
    print("="*60)
    print(f"📄 JSON IN USO:  {os.path.abspath(json_path)}")
    print(f"📡 STAZIONE:     {exp_net}.{exp_sta} (Canale atteso: {exp_chan_prefix}*)")
    print(f"🔴 PICK P:       {p_time_str}")
    print(f"🔵 PICK S:       {s_time_str}")
    print("-" * 60)
    print(f"📈 MINISEED IN CARICAMENTO ({len(mseed_files)} file trovati):")
    for mf in mseed_files:
        print(f"   -> {os.path.abspath(mf)}")
    print("="*60 + "\n")
    # -------------------------------
    
    # Lettura dei file MiniSEED trovati
    raw_stream = Stream()
    for mseed_file in mseed_files:
        try:
            raw_stream += read(mseed_file)
        except Exception as e:
            print(f"Impossibile leggere il file {mseed_file}: {e}")

    if len(raw_stream) == 0:
        print("Errore: Nessuna traccia caricata dai file MiniSEED forniti.")
        sys.exit(1)

    # 3) Verifica della coerenza dei metadati
    valid_stream = Stream()
    for tr in raw_stream:
        net = tr.stats.network
        sta = tr.stats.station
        loc = tr.stats.location
        chan = tr.stats.channel
        
        # Normalizzazione del location code
        loc_is_valid = loc in ["", "--"]
        chan_is_valid = chan.startswith(exp_chan_prefix)
        
        if net == exp_net and sta == exp_sta and loc_is_valid and chan_is_valid:
            valid_stream.append(tr)
        else:
            print(f"Traccia Scartata (Incoerente): {tr.id} -> Atteso: {exp_net}.{exp_sta}.--.{exp_chan_prefix}*")

    if len(valid_stream) == 0:
        print("Errore: Nessuna traccia MiniSEED è coerente con i metadati del JSON.")
        sys.exit(1)

    # 4) Preparazione del Plotting (Z, N, E verticali)
    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(12, 8))
    components = ['Z', 'N', 'E']
    
    # Parsing dei tempi dei Pick sismici dal JSON
    p_pick = UTCDateTime(p_time_str)
    s_pick = UTCDateTime(s_time_str)
    
    # Conversione in formato matplotlib_date
    p_time_md = p_pick.matplotlib_date
    s_time_md = s_pick.matplotlib_date
    
    # Calcolo delle incertezze
    sec_to_days = 1.0 / 86400.0
    p_low = p_time_md - (sta_info['pick_p']['uncertainty_lower'] * sec_to_days)
    p_high = p_time_md + (sta_info['pick_p']['uncertainty_upper'] * sec_to_days)
    
    s_low = s_time_md - (sta_info['pick_s']['uncertainty_lower'] * sec_to_days)
    s_high = s_time_md + (sta_info['pick_s']['uncertainty_upper'] * sec_to_days)
    
    polarity = sta_info['pick_p'].get('polarity', '')

    for idx, comp in enumerate(components):
        ax = axes[idx]
        tr_comp = valid_stream.select(component=comp)
        
        if not tr_comp:
            ax.text(0.5, 0.5, f"Componente {comp} Assente", transform=ax.transAxes, ha='center', va='center', color='gray')
            continue
            
        tr = tr_comp[0]
        
        # Ottimizzazione NumPy: Normalizzazione immediata
        max_amp = np.max(np.abs(tr.data))
        norm_data = tr.data / max_amp if max_amp > 0 else tr.data
        
        times_md = tr.times('matplotlib')
        
        ax.plot(times_md, norm_data, color='k', linewidth=0.8, label=f"Ch: {tr.stats.channel}")
        ax.set_ylabel(f"{comp} (Counts Norm)", fontsize=10)
        
        # --- Configurazione Griglia e Densità Ticks ---
        major_spacing = 5.0 * sec_to_days 
        ax.xaxis.set_major_locator(mticker.MultipleLocator(major_spacing))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(time_formatter))
        
        minor_spacing = 0.5 * sec_to_days
        ax.xaxis.set_minor_locator(mticker.MultipleLocator(minor_spacing))
        
        ax.grid(True, which='major', linestyle='-', color='gray', alpha=0.6, linewidth=0.8)
        ax.grid(True, which='minor', linestyle=':', color='lightgray', alpha=0.5, linewidth=0.5)
        
        # --- Disegno Pick P (Rosso) ---
        ax.axvline(p_time_md, color='red', linestyle='-', linewidth=1.5)
        ax.axvline(p_low, color='red', linestyle='--', linewidth=0.6)
        ax.axvline(p_high, color='red', linestyle='--', linewidth=0.6)
        ax.text(p_time_md, 1.02, f"P ({polarity})", color='red', transform=ax.get_xaxis_transform(), 
                ha='center', va='bottom', fontsize=9, fontweight='bold')
        
        # --- Disegno Pick S (Blu) ---
        ax.axvline(s_time_md, color='blue', linestyle='-', linewidth=1.5)
        ax.axvline(s_low, color='blue', linestyle='--', linewidth=0.6)
        ax.axvline(s_high, color='blue', linestyle='--', linewidth=0.6)
        ax.text(s_time_md, 1.02, "S", color='blue', transform=ax.get_xaxis_transform(), 
                ha='center', va='bottom', fontsize=9, fontweight='bold')
        
        ax.set_ylim(-1.1, 1.1)

    # Finestra temporale intelligente
    zoom_start = p_time_md - (2.0 * sec_to_days)
    zoom_end = s_time_md + (4.0 * sec_to_days)
    plt.xlim(zoom_start, zoom_end)

    fig.suptitle(f"Station: {exp_sta} (Network: {exp_net}) - Stage {args.stage}", fontsize=14, fontweight='bold')
    plt.xlabel("Absolute Time (MM:SS)", fontsize=11)
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
