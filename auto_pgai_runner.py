import os
import sys
import argparse
import subprocess
import shutil
import json
import webbrowser
from pathlib import Path

# Colori per il terminale
C_GREEN = '\033[92m'
C_BLUE = '\033[94m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_END = '\033[0m'

BROWSER_FAILED = False

def resolve_pgai_path() -> Path:
    """
    Risolve dinamicamente il percorso della directory git 'pgai'.
    Cerca un file di configurazione; se non c'è, tenta il fallback su './pgai'.
    Se fallisce tutto, blocca l'esecuzione.
    """
    config_file = Path("path_to_git_pgai.txt")
    default_path = Path("./pgai")
    
    # 1. Controlla se il file custom esiste
    if config_file.exists():
        # Legge il file, toglie gli spazi e a capo, ed espande eventuale '~' (home)
        custom_path_str = config_file.read_text(encoding="utf-8").strip()
        resolved_path = Path(custom_path_str).expanduser()
        
        # Validazione dell'esistenza della cartella custom
        if not resolved_path.is_dir():
            print(f"{C_RED}[ERRORE] Il file {config_file.name} esiste, ma il percorso al suo interno ({resolved_path}) non è una directory valida.{C_END}")
            sys.exit(1)
        return resolved_path

    # 2. Se non c'è il file, prova il fallback
    if default_path.is_dir():
        print(f"{C_YELLOW}[Avviso] File {config_file.name} non trovato. Verrà usata la directory di default: {default_path.resolve()}{C_END}")
        return default_path
        
    # 3. Se nulla funziona, esce con il tuo esatto messaggio
    print(f"{C_RED}non c'è ./pgai, non c'è il file ./path_to_git_pgai.txt ... dunque non posso procedere. Crea il file ./path_to_git_pgai.txt e mettici il full path alla tua dir git di pgai{C_END}")
    sys.exit(1)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Automazione Interattiva PGAI Pipeline")
    parser.add_argument('--eventid', required=True, help="ID dell'evento (es. 46057512)")
    parser.add_argument('--originid', required=False, default=None, help="ID dell'origine (opzionale)")
    parser.add_argument('--gemid', required=True, help="ID del Gem personalizzato (OBBLIGATORIO)")
    return parser.parse_args()

def load_gem_prompts(prompt_filepath: Path) -> tuple[str, str]:
    if not prompt_filepath.exists():
        print(f"{C_YELLOW}[Avviso] File JSON dei prompt non trovato in: {prompt_filepath}{C_END}")
        return "[PROMPT FULL NON TROVATO]", "[PROMPT ZOOM NON TROVATO]"
        
    try:
        with open(prompt_filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        prompt_full = ""
        prompt_zoom = ""
        
        stages = data.get("stages", [])
        for stage in stages:
            if stage.get("level") == "1":
                prompt_full = stage.get("prompt", "")
            elif stage.get("level") == "2":
                prompt_zoom = stage.get("prompt", "")
                
        return prompt_full, prompt_zoom
        
    except Exception as e:
        print(f"{C_RED}[ERRORE] Impossibile leggere o parsare il JSON dei prompt: {e}{C_END}")
        return "[ERRORE LETTURA]", "[ERRORE LETTURA]"

def check_editors_availability():
    if sys.platform.startswith('darwin'):
        pass
    elif os.name == 'nt':
        pass
    elif os.name == 'posix':
        if shutil.which('mousepad') is None:
            print(f"{C_RED}[ERRORE FATALE] L'editor 'mousepad' non è installato su questa macchina Linux.{C_END}")
            print(f"{C_YELLOW}Installalo eseguendo: sudo apt-get install mousepad (o equivalente){C_END}")
            sys.exit(1)

def open_file_in_editor(filepath: Path):
    path_str = str(filepath)
    try:
        if sys.platform.startswith('darwin'):
            subprocess.call(('open', '-a', 'TextEdit', path_str))
        elif os.name == 'nt':
            subprocess.call(('notepad', path_str))
        elif os.name == 'posix':
            subprocess.call(('mousepad', path_str))
    except Exception as e:
        print(f"{C_RED}[Avviso] Impossibile aprire l'editor automaticamente: {e}{C_END}")

def open_gemini_chat(url: str):
    global BROWSER_FAILED
    
    if BROWSER_FAILED:
        return
        
    try:
        chrome_browser = None
        try:
            if sys.platform.startswith('darwin'):
                chrome_browser = webbrowser.get('open -a /Applications/Google\\ Chrome.app %s')
            elif os.name == 'nt':
                chrome_browser = webbrowser.get('chrome')
            elif os.name == 'posix':
                chrome_browser = webbrowser.get('google-chrome')
        except webbrowser.Error:
            chrome_browser = None

        if chrome_browser is not None:
            success = chrome_browser.open_new_tab(url)
        else:
            print(f"\n{C_YELLOW}[AVVISO] Google Chrome non è stato trovato. Verrà usato il browser di default.{C_END}")
            print(f"{C_YELLOW}Si sconsiglia di eseguire la procedura senza Chrome per compatibilità ottimale.{C_END}\n")
            success = webbrowser.open_new_tab(url)

        if not success:
            raise RuntimeError("Nessun browser grafico disponibile.")
            
    except Exception as e:
        print(f"\n{C_YELLOW}Non riesco ad aprire per te la nuova CHAT, fallo tu e poi prosegui. ({e}){C_END}")
        BROWSER_FAILED = True

def is_valid_json(filepath: Path) -> bool:
    if not filepath.exists() or filepath.stat().st_size == 0:
        return False
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            json.load(f)
        return True
    except (json.JSONDecodeError, ValueError):
        return False

def determine_station_level(sta_dir: Path) -> str:
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

def run_waves2pgai_zoom(pgai_base_path: Path, eventid: str, originid: str, sta_dir_name: str, json_path: Path, station_string: str):
    """
    Nota: ora questa funzione richiede il parametro pgai_base_path per sapere dove trovare lo script!
    """
    # Costruiamo il percorso sicuro al file waves2pgai.py
    waves2pgai_script = pgai_base_path / "waves2pgai.py"
    
    cmd = [
        "python3", str(waves2pgai_script),
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
    # 0. Inizializzazione percorsi e setup editor
    pgai_base_path = resolve_pgai_path()
    check_editors_availability()
    args = parse_arguments()

    # Pre-caricamento dei prompt dalla directory corretta
    prompt_file_path = pgai_base_path / "webui_gem_prompts.json"
    prompt_full, prompt_zoom = load_gem_prompts(prompt_file_path)

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
    
    skip_completed = False
    ans_skip = input(f"{C_YELLOW}Vuoi saltare automaticamente le stazioni già completate (Livello 2)? (y/n): {C_END}").strip().lower()
    if ans_skip in ['y', 'yes']:
        skip_completed = True
        print(f"{C_GREEN}Le stazioni complete verranno saltate automaticamente.{C_END}\n")

    for sta_dir in station_dirs:
        initial_level = determine_station_level(sta_dir)
        
        if initial_level == "2" and skip_completed:
            print(f"{C_GREEN}⏭️ SALTO STAZIONE COMPLETATA: {sta_dir.name}{C_END}")
            continue

        parts = sta_dir.name.split('_', 1)
        station_string = parts[1] if len(parts) > 1 else sta_dir.name

        print("="*70)
        print(f"📡 ANALISI STAZIONE: {C_BLUE}{sta_dir.name}{C_END}")
        print("="*70)

        gemini_opened_for_this_station = False

        while True: 
            level = determine_station_level(sta_dir)

            if level == "2":
                print(f"{C_GREEN}[Livello 2] Lavorazione completata (Stage 2 JSON presente).{C_END}")
                ans = input("Passiamo alla prossima stazione? (y/n): ").strip().lower()
                if ans in ['y', 'yes']:
                    break 
                elif ans in ['n', 'no']:
                    print("Uscita dall'automazione.")
                    sys.exit(0)
                else:
                    print("Rispondi 'y' o 'n'.")
                    continue

            if not gemini_opened_for_this_station:
                pgai_url = f"https://gemini.google.com/gem/{args.gemid}"
                open_gemini_chat(pgai_url)
                gemini_opened_for_this_station = True

            if level == "0":
                print(f"{C_YELLOW}[Livello 0] Nessun JSON Stage 1 trovato.{C_END}")
                print(f"\n{C_BLUE}--- COPIA QUESTO PROMPT IN GEMINI (RUN 1) ---{C_END}")
                print(f"{prompt_full}")
                print(f"{C_BLUE}---------------------------------------------{C_END}\n")
                
                ans = input("➡️ Trasporta l'immagine FULL su Gemini assieme al prompt.\nScrivi 'y' e dai INVIO per aprire l'editor: ").strip().lower()
                
                if ans == 'y':
                    stage1_path = sta_dir / f"{sta_dir.name}_stage1.json"
                    stage1_path.touch()
                    open_file_in_editor(stage1_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage1_path.name}' aperto! Incolla l'output, SALVA il file e premi INVIO qui per continuare...{C_END}")
                    
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
                
                if not is_valid_json(stage1_jsons[0]):
                    print(f"{C_RED}[ERRORE] Il file '{stage1_jsons[0].name}' è corrotto o non è un JSON valido. Correggilo manualmente.{C_END}")
                    break

                # Nota: Passiamo pgai_base_path
                success = run_waves2pgai_zoom(pgai_base_path, args.eventid, args.originid, sta_dir.name, stage1_jsons[0], station_string)
                
                if success:
                    continue 
                else:
                    print(f"{C_RED}Generazione zoom fallita. Salto alla prossima stazione.{C_END}")
                    break 

            elif level == "1b":
                print(f"{C_YELLOW}[Livello 1b] File zoom pronti. Manca lo Stage 2.{C_END}")
                print(f"\n{C_BLUE}--- COPIA QUESTO PROMPT IN GEMINI (RUN 2) ---{C_END}")
                print(f"{prompt_zoom}")
                print(f"{C_BLUE}---------------------------------------------{C_END}\n")
                
                ans = input("➡️ Trasporta le immagini ZOOM su Gemini assieme al prompt.\nScrivi 'y' e dai INVIO per aprire il file JSON dello Stage 2: ").strip().lower()
                
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

    print(f"\n{C_GREEN}Tutte le stazioni della cartella sono state processate!{C_END}")

if __name__ == "__main__":
    main()
