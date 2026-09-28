import os
import sys
import argparse
import subprocess
import shutil
import json
import webbrowser
from pathlib import Path

# --- Safe External Dependency Management ---
try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False

# Terminal colors
C_GREEN = '\033[92m'
C_BLUE = '\033[94m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_END = '\033[0m'

BROWSER_FAILED = False

def resolve_pgai_path() -> Path:
    config_file = Path("path_to_git_saian.txt")
    default_path = Path("./saian")
    
    if config_file.exists():
        custom_path_str = config_file.read_text(encoding="utf-8").strip()
        resolved_path = Path(custom_path_str).expanduser()
        
        if not resolved_path.is_dir():
            print(f"{C_RED}[ERROR] File {config_file.name} exists, but the path inside it ({resolved_path}) is not a valid directory.{C_END}")
            sys.exit(1)
        return resolved_path

    if default_path.is_dir():
        print(f"{C_YELLOW}[Warning] File {config_file.name} not found. The default directory will be used: {default_path.resolve()}{C_END}")
        return default_path
        
    print(f"{C_RED}There is no ./pgai, there is no ./path_to_git_pgai.txt file... therefore I cannot proceed. Create the ./path_to_git_pgai.txt file and put the full path to your pgai git dir in it{C_END}")
    sys.exit(1)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Interactive PGAI Pipeline Automation")
    parser.add_argument('--eventid', required=True, help="Event ID (e.g., 46057512)")
    parser.add_argument('--originid', required=False, default=None, help="Origin ID (optional)")
    parser.add_argument('--gemid', required=True, help="Custom Gem ID (MANDATORY)")
    return parser.parse_args()

def load_gem_prompts(prompt_filepath: Path) -> tuple[str, str]:
    if not prompt_filepath.exists():
        print(f"{C_YELLOW}[Warning] Prompts JSON file not found at: {prompt_filepath}{C_END}")
        return "[FULL PROMPT NOT FOUND]", "[ZOOM PROMPT NOT FOUND]"
        
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
        print(f"{C_RED}[ERROR] Unable to read or parse the prompts JSON: {e}{C_END}")
        return "[READ ERROR]", "[READ ERROR]"

def check_editors_availability():
    if sys.platform.startswith('darwin'):
        pass
    elif os.name == 'nt':
        pass
    elif os.name == 'posix':
        if shutil.which('mousepad') is None:
            print(f"{C_RED}[FATAL ERROR] The 'mousepad' editor is not installed on this Linux machine.{C_END}")
            print(f"{C_YELLOW}Install it by running: sudo apt-get install mousepad (or equivalent){C_END}")
            sys.exit(1)

# =====================================================================
# UNIVERSAL OPENING FUNCTIONS (NON-BLOCKING)
# =====================================================================
def open_directory(dir_path: Path):
    """
    Opens the specified directory in the OS native file manager asynchronously.
    Supports macOS (open), Windows (os.startfile), and Linux (xdg-open).
    """
    abs_path_str = str(dir_path.resolve())
    print(f"{C_BLUE}   -> [OS] Requesting Directory open for: {dir_path.name}{C_END}")
    
    try:
        if sys.platform.startswith('darwin'):  # macOS
            subprocess.Popen(['open', abs_path_str])
        elif os.name == 'nt':  # Windows
            os.startfile(abs_path_str)
        elif os.name == 'posix':  # Linux
            subprocess.Popen(['xdg-open', abs_path_str], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"{C_RED}   [Warning] OS exception while opening directory: {e}{C_END}")

def open_file_in_editor(filepath: Path):
    """
    Opens a file using the hard-coded text editor in a fully detached mode.
    """
    abs_path_str = str(filepath.resolve())
    
    try:
        if sys.platform.startswith('darwin'):  # macOS
            subprocess.Popen(['open', '-e', abs_path_str])
        elif os.name == 'nt':  # Windows
            subprocess.Popen(['notepad', abs_path_str])
        elif os.name == 'posix':  # Linux
            subprocess.Popen(['mousepad', abs_path_str], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"{C_RED}[Warning] Cannot open the editor automatically: {e}{C_END}")
# =====================================================================

def open_gemini_chat(url: str):
    global BROWSER_FAILED
    if BROWSER_FAILED: return
        
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
            print(f"\n{C_YELLOW}[WARNING] Google Chrome was not found. The default browser will be used.{C_END}")
            success = webbrowser.open_new_tab(url)

        if not success:
            raise RuntimeError("No graphical browser available.")
            
    except Exception as e:
        print(f"\n{C_YELLOW}I cannot open the new CHAT for you, please do it yourself and then proceed. ({e}){C_END}")
        BROWSER_FAILED = True

def copy_prompt_to_clipboard(prompt_text: str):
    if HAS_PYPERCLIP:
        try:
            pyperclip.copy(prompt_text)
            print(f"{C_GREEN}✅ Prompt automatically copied to clipboard! (Use Cmd+V / Ctrl+V in Gemini){C_END}")
        except Exception as e:
            print(f"{C_YELLOW}⚠️ Cannot access clipboard: {e}. Copy the text manually.{C_END}")
    else:
        print(f"{C_YELLOW}💡 Tip: Install 'pyperclip' (pip install pyperclip) to copy the prompt automatically.{C_END}")


# --- NEW: Enhanced JSON State Checker ---
def check_json_state(filepath: Path) -> str:
    """
    Returns the exact state of the JSON file to allow for granular error handling.
    Returns: "MISSING", "EMPTY", "INVALID", or "VALID".
    """
    if not filepath.exists(): 
        return "MISSING"
    if filepath.stat().st_size == 0: 
        return "EMPTY"
    try:
        with open(filepath, 'r', encoding='utf-8') as f: 
            json.load(f)
        return "VALID"
    except (json.JSONDecodeError, ValueError): 
        return "INVALID"
# ----------------------------------------

def determine_station_level(sta_dir: Path) -> str:
    stage1_jsons = list(sta_dir.glob("*_stage1.json"))
    stage2_jsons = list(sta_dir.glob("*_stage2.json"))
    zoom_pngs = list(sta_dir.glob("*zoom_*.png"))

    if stage2_jsons: return "2"
    elif stage1_jsons and zoom_pngs: return "1b"
    elif stage1_jsons and not zoom_pngs: return "1a"
    else: return "0"

def run_waves2saian_zoom(pgai_base_path: Path, eventid: str, originid: str, sta_dir_name: str, json_path: Path, station_string: str):
    waves2saian_script = pgai_base_path / "waves2saian.py"
    
    cmd = [
        "python3", str(waves2saian_script),
        "--config", "./saian_config.json",
        "--eventid", eventid,
        "--ai-picks-json", str(json_path),
        "--zoom",
        "--zoom-levels", "context",
        "--stations", station_string,
        "--expand-dynamics",
        "--filter", "suggested"
    ]
    if originid: cmd.extend(["--originid", originid])

    print(f"{C_BLUE}Execution in progress: {' '.join(cmd)}{C_END}")
    
    try:
        subprocess.run(cmd, check=True)
        print(f"{C_GREEN}[OK] Zoom processing completed successfully.{C_END}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"{C_RED}[ERROR] The waves2saian command returned an error: {e}{C_END}")
        return False

def main():
    pgai_base_path = resolve_pgai_path()
    check_editors_availability()
    args = parse_arguments()

    prompt_file_path = pgai_base_path / "webui_gem_prompts.json"
    prompt_full, prompt_zoom = load_gem_prompts(prompt_file_path)

    event_dir_name = f"waveforms_event_eid{args.eventid}_oid{args.originid}" if args.originid else f"waveforms_event_eid{args.eventid}"
    event_dir = Path(event_dir_name)

    if not event_dir.exists() or not event_dir.is_dir():
        print(f"{C_RED}Error: Event directory '{event_dir}' does not exist.{C_END}")
        sys.exit(1)

    station_dirs = [d for d in event_dir.iterdir() if d.is_dir() and "stations_xml" not in d.name]
    station_dirs.sort()

    if not station_dirs:
        print(f"{C_YELLOW}No station directory found in '{event_dir}'.{C_END}")
        sys.exit(0)

    print(f"\n{C_GREEN}Found {len(station_dirs)} stations for event {args.eventid}.{C_END}")
    
    skip_completed = False
    ans_skip = input(f"{C_YELLOW}Do you want to automatically skip already completed stations (Level 2)? (y/n): {C_END}").strip().lower()
    if ans_skip in ['y', 'yes']:
        skip_completed = True
        print(f"{C_GREEN}Completed stations will be skipped automatically.{C_END}\n")

    for sta_dir in station_dirs:
        initial_level = determine_station_level(sta_dir)
        
        # Fast-forward past completed stations
        if initial_level == "2":
            stage2_path = list(sta_dir.glob("*_stage2.json"))[0]
            if check_json_state(stage2_path) == "VALID" and skip_completed:
                print(f"{C_GREEN}⏭️ SKIPPING COMPLETED STATION: {sta_dir.name}{C_END}")
                continue

        parts = sta_dir.name.split('_', 1)
        station_string = parts[1] if len(parts) > 1 else sta_dir.name

        print("="*70)
        print(f"📡 STATION ANALYSIS: {C_BLUE}{sta_dir.name}{C_END}")
        print("="*70)

        # Local flags to manage UI state within the current station loop
        gemini_opened_for_this_station = False
        finder_opened_for_this_stage = False

        while True: 
            level = determine_station_level(sta_dir)

            # ---------------------------------------------------------
            # LEVEL 2: VALIDATE STAGE 2 JSON
            # ---------------------------------------------------------
            if level == "2":
                stage2_path = list(sta_dir.glob("*_stage2.json"))[0]
                state = check_json_state(stage2_path)
                
                if state == "VALID":
                    print(f"{C_GREEN}[Level 2] Processing completed (Stage 2 JSON is valid).{C_END}")
                    ans = input("Move on to the next station? (y/n): ").strip().lower()
                    if ans in ['y', 'yes']:
                        break 
                    elif ans in ['n', 'no']:
                        print("Exiting automation.")
                        sys.exit(0)
                    else:
                        print("Answer 'y' or 'n'.")
                        continue
                
                elif state == "EMPTY":
                    print(f"{C_RED}[ERROR] The Stage 2 JSON file '{stage2_path.name}' is empty.{C_END}")
                    ans = input(f"{C_YELLOW}Do you want to (d)elete and restart this stage, or (s)kip station? (d/s): {C_END}").strip().lower()
                    if ans == 'd':
                        stage2_path.unlink() # Deleting reverts level to 1b!
                        finder_opened_for_this_stage = False
                        continue
                    elif ans == 's':
                        break
                        
                elif state == "INVALID":
                    print(f"{C_RED}[ERROR] The Stage 2 JSON file '{stage2_path.name}' is malformed.{C_END}")
                    ans = input(f"{C_YELLOW}Do you want to (d)elete & restart stage, (c)orrect manually, or (s)kip station? (d/c/s): {C_END}").strip().lower()
                    if ans == 'd':
                        stage2_path.unlink() # Deleting reverts level to 1b!
                        finder_opened_for_this_stage = False
                        continue
                    elif ans == 'c':
                        open_file_in_editor(stage2_path)
                        input(f"{C_BLUE}➡️ Correct the JSON, SAVE the file, and press ENTER to re-evaluate...{C_END}")
                        continue
                    elif ans == 's':
                        break

            # ---------------------------------------------------------
            # OPEN GEMINI (Only once per station, right before active work)
            # ---------------------------------------------------------
            if not gemini_opened_for_this_station:
                pgai_url = f"https://gemini.google.com/gem/{args.gemid}"
                open_gemini_chat(pgai_url)
                gemini_opened_for_this_station = True

            # ---------------------------------------------------------
            # LEVEL 0: CREATE STAGE 1 JSON
            # ---------------------------------------------------------
            if level == "0":
                if not finder_opened_for_this_stage:
                    print(f"{C_YELLOW}[Level 0] No Stage 1 JSON found.{C_END}")
                    print(f"\n{C_BLUE}--- PROMPT IN GEMINI (RUN 1) ---{C_END}")
                    print(f"{prompt_full}")
                    print(f"{C_BLUE}--------------------------------{C_END}\n")
                    
                    copy_prompt_to_clipboard(prompt_full)
                    open_directory(sta_dir)
                    finder_opened_for_this_stage = True
                
                ans = input("\n➡️ Drag the FULL image to Gemini along with the prompt.\nType 'y' to open editor, or 's' to skip this station: ").strip().lower()
                
                if ans == 'y':
                    stage1_path = sta_dir / f"{sta_dir.name}_stage1.json"
                    stage1_path.touch()
                    open_file_in_editor(stage1_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage1_path.name}' opened! Paste the output, SAVE the file and press ENTER here to continue...{C_END}")
                    
                    # We do NOT validate here. The loop continues and becomes Level 1a.
                    # Level 1a will handle validation at its entry gate.
                    finder_opened_for_this_stage = False
                    continue
                elif ans == 's':
                    print(f"{C_YELLOW}Skipping station {sta_dir.name} and moving to the next one.{C_END}")
                    break
                else:
                    print("Input not recognized. Try again.")

            # ---------------------------------------------------------
            # LEVEL 1A: VALIDATE STAGE 1 JSON & GENERATE ZOOMS
            # ---------------------------------------------------------
            elif level == "1a":
                stage1_path = list(sta_dir.glob("*_stage1.json"))[0]
                state = check_json_state(stage1_path)
                
                if state == "VALID":
                    print(f"{C_YELLOW}[Level 1a] Stage 1 JSON is present and valid.{C_END}")
                    ans = input("➡️ I will proceed to generate the zooms. Press ENTER to start, or 's' to skip: ").strip().lower()
                    if ans == 's': break
                    
                    success = run_waves2saian_zoom(pgai_base_path, args.eventid, args.originid, sta_dir.name, stage1_path, station_string)
                    if success:
                        finder_opened_for_this_stage = False
                        continue 
                    else:
                        print(f"{C_RED}Zoom generation failed. Skipping to the next station.{C_END}")
                        break 
                        
                elif state == "EMPTY":
                    print(f"{C_RED}[ERROR] The Stage 1 JSON file '{stage1_path.name}' is empty.{C_END}")
                    ans = input(f"{C_YELLOW}Do you want to (d)elete and restart this station from zero, or (s)kip station? (d/s): {C_END}").strip().lower()
                    if ans == 'd':
                        stage1_path.unlink() # Reverts level to 0
                        finder_opened_for_this_stage = False
                        continue
                    elif ans == 's':
                        break
                        
                elif state == "INVALID":
                    print(f"{C_RED}[ERROR] The Stage 1 JSON file '{stage1_path.name}' is malformed.{C_END}")
                    ans = input(f"{C_YELLOW}Do you want to (d)elete & restart from scratch, (c)orrect manually, or (s)kip station? (d/c/s): {C_END}").strip().lower()
                    if ans == 'd':
                        stage1_path.unlink() # Reverts level to 0
                        finder_opened_for_this_stage = False
                        continue
                    elif ans == 'c':
                        open_file_in_editor(stage1_path)
                        input(f"{C_BLUE}➡️ Correct the JSON, SAVE the file, and press ENTER to re-evaluate...{C_END}")
                        continue
                    elif ans == 's':
                        break

            # ---------------------------------------------------------
            # LEVEL 1B: CREATE STAGE 2 JSON
            # ---------------------------------------------------------
            elif level == "1b":
                if not finder_opened_for_this_stage:
                    print(f"{C_YELLOW}[Level 1b] Zoom files ready. Stage 2 is missing.{C_END}")
                    print(f"\n{C_BLUE}--- PROMPT IN GEMINI (RUN 2) ---{C_END}")
                    print(f"{prompt_zoom}")
                    print(f"{C_BLUE}--------------------------------{C_END}\n")
                    
                    copy_prompt_to_clipboard(prompt_zoom)
                    open_directory(sta_dir)
                    finder_opened_for_this_stage = True
                
                ans = input("\n➡️ Drag the ZOOM images to Gemini along with the prompt.\nType 'y' to open Stage 2 JSON, or 's' to skip: ").strip().lower()
                
                if ans == 'y':
                    stage2_path = sta_dir / f"{sta_dir.name}_stage2.json"
                    stage2_path.touch()
                    open_file_in_editor(stage2_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage2_path.name}' opened! Paste the output, SAVE the file and press ENTER here to continue...{C_END}")
                    
                    # We do NOT validate here. The loop continues and becomes Level 2.
                    # Level 2 will handle validation at its entry gate.
                    finder_opened_for_this_stage = False
                    continue 
                elif ans == 's':
                    print(f"{C_YELLOW}Skipping station {sta_dir.name} and moving to the next one.{C_END}")
                    break
                else:
                    print("Input not recognized. Try again.")

    print(f"\n{C_GREEN}All stations in the folder have been processed!{C_END}")

if __name__ == "__main__":
    main()
