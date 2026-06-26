import argparse
import json
import os
import sys
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import obspy
from obspy import Stream, read, UTCDateTime

def parse_arguments():
    parser = argparse.ArgumentParser(description="Validazione e Plotting di Forme d'Onda Sismiche.")
    parser.add_argument('--json_in', required=True, help="Percorso del file JSON di input")
    parser.add_argument('--mseed_in', required=True, help="Lista di file MiniSEED separati da virgola (es. f1.mseed,f2.mseed)")
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
    event_data = load_json_data(args.json_in)
    
    # Estrazione metadati attesi dal JSON (assumendo una stazione per file come da struttura)
    sta_info = event_data['stations'][0]
    exp_net = sta_info['network']
    exp_sta = sta_info['stacode']
    exp_chan_prefix = sta_info['channel_code']
    
    # Parsing dei file MiniSEED
    mseed_files = [f.strip() for f in args.mseed_in.split(',')]
    raw_stream = Stream()
    
    for mseed_file in mseed_files:
        if os.path.exists(mseed_file):
            try:
                raw_stream += read(mseed_file)
            except Exception as e:
                print(f"Impossibile leggere il file {mseed_file}: {e}")
        else:
            print(f"Avviso: File MiniSEED '{mseed_file}' non trovato.")

    if len(raw_stream) == 0:
        print("Errore: Nessuna traccia caricata dai file MiniSEED forniti.")
        sys.exit(1)

    # 1) Verifica della coerenza dei metadati
    valid_stream = Stream()
    for tr in raw_stream:
        net = tr.stats.network
        sta = tr.stats.station
        loc = tr.stats.location
        chan = tr.stats.channel
        
        # Normalizzazione del location code (vuoto o "--" sono considerati equivalenti)
        loc_is_valid = loc in ["", "--"]
        chan_is_valid = chan.startswith(exp_chan_prefix)
        
        if net == exp_net and sta == exp_sta and loc_is_valid and chan_is_valid:
            valid_stream.append(tr)
        else:
            print(f"Traccia Scartata (Incoerente): {tr.id} -> Atteso: {exp_net}.{exp_sta}.--.{exp_chan_prefix}*")

    if len(valid_stream) == 0:
        print("Errore: Nessuna traccia MiniSEED è coerente con i metadati del JSON.")
        sys.exit(1)

    # 2) Preparazione del Plotting (Z, N, E verticali)
    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(12, 8))
    components = ['Z', 'N', 'E']
    
    # Parsing dei tempi dei Pick sismici dal JSON
    p_pick = UTCDateTime(sta_info['pick_p']['time'])
    s_pick = UTCDateTime(sta_info['pick_s']['time'])
    
    # Conversione in formato matplotlib_date per la gestione degli assi
    p_time_md = p_pick.matplotlib_date
    s_time_md = s_pick.matplotlib_date
    
    # Calcolo delle incertezze (in giorni, l'unità di misura di matplotlib dates)
    sec_to_days = 1.0 / 86400.0
    p_low = p_time_md - (sta_info['pick_p']['uncertainty_lower'] * sec_to_days)
    p_high = p_time_md + (sta_info['pick_p']['uncertainty_upper'] * sec_to_days)
    
    s_low = s_time_md - (sta_info['pick_s']['uncertainty_lower'] * sec_to_days)
    s_high = s_time_md + (sta_info['pick_s']['uncertainty_upper'] * sec_to_days)
    
    polarity = sta_info['pick_p'].get('polarity', '')

    for idx, comp in enumerate(components):
        ax = axes[idx]
        # Selezione della componente specifica (es. HHZ, EHZ, etc.)
        tr_comp = valid_stream.select(component=comp)
        
        if not tr_comp:
            ax.text(0.5, 0.5, f"Componente {comp} Assente", transform=ax.transAxes, ha='center', va='center', color='gray')
            continue
            
        tr = tr_comp[0] # Prende la prima traccia coerente trovata
        
        # Ottimizzazione NumPy: Normalizzazione immediata all'ampiezza massima assoluta
        max_amp = np.max(np.abs(tr.data))
        norm_data = tr.data / max_amp if max_amp > 0 else tr.data
        
        # Vettore dei tempi assoluti in formato matplotlib date
        times_md = tr.times('matplotlib')
        
        # Plot della waveform (linea nera)
        ax.plot(times_md, norm_data, color='k', linewidth=0.8, label=f"Ch: {tr.stats.channel}")
        ax.set_ylabel(f"{comp} (Counts Norm)", fontsize=10)
        
        # --- Configurazione Griglia e Densità Ticks ---
        # 1. Ticks Principali (con Label) ogni 5.0 secondi
        major_spacing = 5.0 * sec_to_days 
        ax.xaxis.set_major_locator(mticker.MultipleLocator(major_spacing))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(time_formatter))
        
        # 2. Ticks Secondari (solo linee, senza testo) ogni 0.5 secondi per densità
        minor_spacing = 0.5 * sec_to_days
        ax.xaxis.set_minor_locator(mticker.MultipleLocator(minor_spacing))
        
        # 3. Disegno differenziato della griglia per una migliore leggibilità
        ax.grid(True, which='major', linestyle='-', color='gray', alpha=0.6, linewidth=0.8)
        ax.grid(True, which='minor', linestyle=':', color='lightgray', alpha=0.5, linewidth=0.5)
        
        # --- Disegno Pick P (Rosso) ---
        ax.axvline(p_time_md, color='red', linestyle='-', linewidth=1.5)
        ax.axvline(p_low, color='red', linestyle='--', linewidth=0.6)
        ax.axvline(p_high, color='red', linestyle='--', linewidth=0.6)
        # Label P sopra l'asse
        ax.text(p_time_md, 1.02, f"P ({polarity})", color='red', transform=ax.get_xaxis_transform(), 
                ha='center', va='bottom', fontsize=9, fontweight='bold')
        
        # --- Disegno Pick S (Blu) ---
        ax.axvline(s_time_md, color='blue', linestyle='-', linewidth=1.5)
        ax.axvline(s_low, color='blue', linestyle='--', linewidth=0.6)
        ax.axvline(s_high, color='blue', linestyle='--', linewidth=0.6)
        # Label S sopra l'asse
        ax.text(s_time_md, 1.02, "S", color='blue', transform=ax.get_xaxis_transform(), 
                ha='center', va='bottom', fontsize=9, fontweight='bold')
        
        # Set dei limiti Y normalizzati
        ax.set_ylim(-1.1, 1.1)

    # Finestra temporale intelligente intorno ai pick (es. 2s prima di P e 4s dopo S) per visualizzare la griglia 0.1s
    zoom_start = p_time_md - (2.0 * sec_to_days)
    zoom_end = s_time_md + (4.0 * sec_to_days)
    plt.xlim(zoom_start, zoom_end)

    # Titolo globale del grafico
    fig.suptitle(f"Station: {exp_sta} (Network: {exp_net})", fontsize=14, fontweight='bold')
    plt.xlabel("Absolute Time (MM:SS)", fontsize=11)
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
