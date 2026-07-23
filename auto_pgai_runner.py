import os
import sys
import argparse
import subprocess
import shutil
import json
from pathlib import Path

# Colori per il terminale
C_GREEN = '\033[92m'
C_BLUE = '\033[94m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_END = '\033[0m'

def parse_arguments():
    parser = argparse.ArgumentParser(description="Automazione Interattiva PGAI Pipeline")
    parser.add_argument('--eventid', required=True, help="ID dell'evento (es. 46057512)")
    parser.add_argument('--originid', required=False, default=None, help="ID dell'origine (opzionale)")
    return parser.parse_args()

def check_editors_availability():
    """
    Verifica che gli editor testuali hard-coded siano disponibili nel sistema.
    """
    if sys.platform.startswith('darwin'):
        # Su Mac, TextEdit è nativo, assumiamo che ci sia.
        pass
    elif os.name == 'nt':
        # Su Windows, Notepad è nativo.
        pass
    elif os.name == 'posix':
        # Su Linux, controlliamo in modo efficiente se mousepad è nel PATH
        if shutil.which('mousepad') is None:
            print(f"{C_RED}[ERRORE FATALE] L'editor 'mousepad' non è installato su questa macchina Linux.{C_END}")
            print(f"{C_YELLOW}Installalo eseguendo: sudo apt-get install mousepad (o equivalente){C_END}")
            sys.exit(1)

def open_file_in_editor(filepath: Path):
    """
    Apre un file forzando l'editor di testo hard-coded del sistema operativo.
    """
    path_str = str(filepath)
    try:
        if sys.platform.startswith('darwin'):  # macOS
            subprocess.call(('open', '-a', 'TextEdit', path_str))
        elif os.name == 'nt':  # Windows
            subprocess.call(('notepad', path_str))
        elif os.name == 'posix':  # Linux
            subprocess.call(('mousepad', path_str))
    except Exception as e:
        print(f"{C_RED}[Avviso] Impossibile aprire l'editor automaticamente: {e}{C_END}")

def is_valid_json(filepath: Path) -> bool:
    """
    Legge il file appena salvato dall'utente per assicurarsi che non sia vuoto
    e che contenga un JSON formattato correttamente, evitando crash a valle.
    """
    if not filepath.exists() or filepath.stat().st_size == 0:
        return False
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            json.load(f)
        return True
    except (json.JSONDecodeError, ValueError):
        return False

def determine_station_level(sta_dir: Path) -> str:
    """
    Analizza il contenuto della directory per determinare il livello di lavorazione.
    """
    stage1_jsons = list(sta_dir.glob("*_stage1.json"))
    stage2_jsons = list(sta_dir.glob("*_stage2.json"))
    zoom_pngs = list(sta_dir.glob("*zoom_*.png"))

    if stage2_jsons:
        return "2"
    elif stage1_jsons and zoom_pngs:
        return "1b"
    elif stage1_jsons and not zoom_pngs:
        return "1a"
    else:
        return "0"

def run_waves2pgai_zoom(eventid: str, originid: str, sta_dir_name: str, json_path: Path, station_string: str):
    """
    Lancia il comando subprocess per generare gli zoom tramite waves2pgai.py.
    """
    cmd = [
        "python3", "pgai/waves2pgai.py",
        "--config", "./pgai_config.json",
        "--eventid", eventid,
        "--ai-picks-json", str(json_path),
        "--zoom",
        "--zoom-levels", "context",
        "--stations", station_string,
        "--expand-dynamics",
        "--filter", "suggested"
    ]
    if originid:
        cmd.extend(["--originid", originid])

    print(f"{C_BLUE}Esecuzione in corso: {' '.join(cmd)}{C_END}")
    
    try:
        subprocess.run(cmd, check=True)
        print(f"{C_GREEN}[OK] Elaborazione zoom terminata con successo.{C_END}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"{C_RED}[ERRORE] Il comando waves2pgai ha restituito un errore: {e}{C_END}")
        return False

def main():
    # 0. Verifica preventiva degli editor testuali sul sistema
    check_editors_availability()

    args = parse_arguments()

    if args.originid:
        event_dir_name = f"waveforms_event_eid{args.eventid}_oid{args.originid}"
    else:
        event_dir_name = f"waveforms_event_eid{args.eventid}" 
    
    event_dir = Path(event_dir_name)

    if not event_dir.exists() or not event_dir.is_dir():
        print(f"{C_RED}Errore: La directory evento '{event_dir}' non esiste.{C_END}")
        sys.exit(1)

    station_dirs = [d for d in event_dir.iterdir() if d.is_dir() and "stations_xml" not in d.name]
    station_dirs.sort()

    if not station_dirs:
        print(f"{C_YELLOW}Nessuna directory di stazione trovata in '{event_dir}'.{C_END}")
        sys.exit(0)

    print(f"\n{C_GREEN}Trovate {len(station_dirs)} stazioni per l'evento {args.eventid}.{C_END}")
    
    # Richiesta preferenza di skippaggio per il Livello 2
    skip_completed = False
    ans_skip = input(f"{C_YELLOW}Vuoi saltare automaticamente le stazioni già completate (Livello 2)? (y/n): {C_END}").strip().lower()
    if ans_skip in ['y', 'yes']:
        skip_completed = True
        print(f"{C_GREEN}Le stazioni complete verranno saltate automaticamente.{C_END}\n")

    for sta_dir in station_dirs:
        parts = sta_dir.name.split('_', 1)
        station_string = parts[1] if len(parts) > 1 else sta_dir.name

        print("="*70)
        print(f"📡 ANALISI STAZIONE: {C_BLUE}{sta_dir.name}{C_END}")
        print("="*70)

        while True: 
            level = determine_station_level(sta_dir)

            if level == "0":
                print(f"{C_YELLOW}[Livello 0] Nessun JSON Stage 1 trovato.{C_END}")
                ans = input("➡️ Trasporta l'immagine FULL su Gemini.\nScrivi 'y' e dai INVIO per aprire l'editor: ").strip().lower()
                
                if ans == 'y':
                    stage1_path = sta_dir / f"{sta_dir.name}_stage1.json"
                    stage1_path.touch()
                    open_file_in_editor(stage1_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage1_path.name}' aperto! Incolla l'output, SALVA il file e premi INVIO qui per continuare...{C_END}")
                    
                    # Pre-validazione essenziale
                    if not is_valid_json(stage1_path):
                        print(f"{C_RED}[ERRORE] Il file è vuoto o il JSON non è valido! Controlla di aver incollato e salvato correttamente.{C_END}")
                        continue
                    continue
                else:
                    print("Input non riconosciuto. Riprova.")

            elif level == "1a":
                print(f"{C_YELLOW}[Livello 1a] JSON Stage 1 presente, ma mancano i file zoom.{C_END}")
                input("➡️ Procederò a generare gli zoom. Premi INVIO per avviare...")
                
                stage1_jsons = list(sta_dir.glob("*_stage1.json"))
                
                # Ulteriore controllo di sicurezza sul json esistente prima di lanciarlo nel loop
                if not is_valid_json(stage1_jsons[0]):
                    print(f"{C_RED}[ERRORE] Il file '{stage1_jsons[0].name}' è corrotto o non è un JSON valido. Correggilo manualmente prima di procedere.{C_END}")
                    break

                success = run_waves2pgai_zoom(args.eventid, args.originid, sta_dir.name, stage1_jsons[0], station_string)
                
                if success:
                    continue 
                else:
                    print(f"{C_RED}Generazione zoom fallita. Salto alla prossima stazione.{C_END}")
                    break 

            elif level == "1b":
                print(f"{C_YELLOW}[Livello 1b] File zoom pronti. Manca lo Stage 2.{C_END}")
                ans = input("➡️ Trasporta le immagini ZOOM su Gemini.\nScrivi 'y' e dai INVIO per aprire il file JSON dello Stage 2: ").strip().lower()
                
                if ans == 'y':
                    stage2_path = sta_dir / f"{sta_dir.name}_stage2.json"
                    stage2_path.touch()
                    open_file_in_editor(stage2_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage2_path.name}' aperto! Incolla l'output, SALVA il file e premi INVIO qui per continuare...{C_END}")
                    
                    if not is_valid_json(stage2_path):
                        print(f"{C_RED}[ERRORE] Il file è vuoto o il JSON non è valido! Controlla di aver incollato e salvato correttamente.{C_END}")
                        continue
                    continue 
                else:
                    print("Input non riconosciuto. Riprova.")

            elif level == "2":
                if skip_completed:
                    print(f"{C_GREEN}[Livello 2] Lavorazione completata. Salto automatico...{C_END}")
                    break
                else:
                    print(f"{C_GREEN}[Livello 2] Lavorazione completata (Stage 2 JSON presente).{C_END}")
                    ans = input("Passiamo alla prossima stazione? (y/n): ").strip().lower()
                    if ans in ['y', 'yes']:
                        break 
                    elif ans in ['n', 'no']:
                        print("Uscita dall'automazione.")
                        sys.exit(0)
                    else:
                        print("Rispondi 'y' o 'n'.")

    print(f"\n{C_GREEN}Tutte le stazioni della cartella sono state processate!{C_END}")

if __name__ == "__main__":
    main()
