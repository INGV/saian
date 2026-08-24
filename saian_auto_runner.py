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

def open_file_in_editor(filepath: Path):
    """
    Opens a file forcing the hard-coded editor.
    Uses Popen on Linux to detach the graphical process and converts paths to absolute.
    """
    # 1. Optimization: Always transform the path to absolute
    abs_path_str = str(filepath.resolve())
    
    try:
        if sys.platform.startswith('darwin'):  # macOS
            subprocess.call(('open', '-a', 'TextEdit', abs_path_str))
        elif os.name == 'nt':  # Windows
            subprocess.call(('notepad', abs_path_str))
        elif os.name == 'posix':  # Linux
            # 2. Optimization: We use Popen (non-blocking) and silence the streams
            subprocess.Popen(
                ['mousepad', abs_path_str],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except Exception as e:
        print(f"{C_RED}[Warning] Cannot open the editor automatically: {e}{C_END}")

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
            print(f"\n{C_YELLOW}[WARNING] Google Chrome was not found. The default browser will be used.{C_END}")
            print(f"{C_YELLOW}It is not recommended to run the procedure without Chrome for optimal compatibility.{C_END}\n")
            success = webbrowser.open_new_tab(url)

        if not success:
            raise RuntimeError("No graphical browser available.")
            
    except Exception as e:
        print(f"\n{C_YELLOW}I cannot open the new CHAT for you, please do it yourself and then proceed. ({e}){C_END}")
        BROWSER_FAILED = True

def copy_prompt_to_clipboard(prompt_text: str):
    """
    Attempts to copy text to the clipboard if pyperclip is installed.
    Fails safely without blocking the script if it is not.
    """
    if HAS_PYPERCLIP:
        try:
            pyperclip.copy(prompt_text)
            print(f"{C_GREEN}✅ Prompt automatically copied to clipboard! (Use Cmd+V / Ctrl+V in Gemini){C_END}")
        except Exception as e:
            print(f"{C_YELLOW}⚠️ Cannot access clipboard: {e}. Copy the text manually.{C_END}")
    else:
        print(f"{C_YELLOW}💡 Tip: Install 'pyperclip' (pip install pyperclip) to copy the prompt automatically.{C_END}")


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
    if originid:
        cmd.extend(["--originid", originid])

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

    if args.originid:
        event_dir_name = f"waveforms_event_eid{args.eventid}_oid{args.originid}"
    else:
        event_dir_name = f"waveforms_event_eid{args.eventid}" 
    
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
        
        if initial_level == "2" and skip_completed:
            print(f"{C_GREEN}⏭️ SKIPPING COMPLETED STATION: {sta_dir.name}{C_END}")
            continue

        parts = sta_dir.name.split('_', 1)
        station_string = parts[1] if len(parts) > 1 else sta_dir.name

        print("="*70)
        print(f"📡 STATION ANALYSIS: {C_BLUE}{sta_dir.name}{C_END}")
        print("="*70)

        gemini_opened_for_this_station = False

        while True: 
            level = determine_station_level(sta_dir)

            if level == "2":
                print(f"{C_GREEN}[Level 2] Processing completed (Stage 2 JSON present).{C_END}")
                ans = input("Move on to the next station? (y/n): ").strip().lower()
                if ans in ['y', 'yes']:
                    break 
                elif ans in ['n', 'no']:
                    print("Exiting automation.")
                    sys.exit(0)
                else:
                    print("Answer 'y' or 'n'.")
                    continue

            if not gemini_opened_for_this_station:
                pgai_url = f"https://gemini.google.com/gem/{args.gemid}"
                open_gemini_chat(pgai_url)
                gemini_opened_for_this_station = True

            if level == "0":
                print(f"{C_YELLOW}[Level 0] No Stage 1 JSON found.{C_END}")
                print(f"\n{C_BLUE}--- PROMPT IN GEMINI (RUN 1) ---{C_END}")
                print(f"{prompt_full}")
                print(f"{C_BLUE}--------------------------------{C_END}\n")
                
                # --- Safe automatic copy to clipboard ---
                copy_prompt_to_clipboard(prompt_full)
                
                ans = input("\n➡️ Drag the FULL image to Gemini along with the prompt.\nType 'y' and press ENTER to open the editor: ").strip().lower()
                
                if ans == 'y':
                    stage1_path = sta_dir / f"{sta_dir.name}_stage1.json"
                    stage1_path.touch()
                    open_file_in_editor(stage1_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage1_path.name}' opened! Paste the output, SAVE the file and press ENTER here to continue...{C_END}")
                    
                    if not is_valid_json(stage1_path):
                        print(f"{C_RED}[ERROR] The file is empty or the JSON is invalid! Check that you pasted and saved correctly.{C_END}")
                        continue
                    continue
                else:
                    print("Input not recognized. Try again.")

            elif level == "1a":
                print(f"{C_YELLOW}[Level 1a] Stage 1 JSON present, but zoom files are missing.{C_END}")
                input("➡️ I will proceed to generate the zooms. Press ENTER to start...")
                
                stage1_jsons = list(sta_dir.glob("*_stage1.json"))
                
                if not is_valid_json(stage1_jsons[0]):
                    print(f"{C_RED}[ERROR] The file '{stage1_jsons[0].name}' is corrupted or not a valid JSON. Correct it manually.{C_END}")
                    break

                success = run_waves2saian_zoom(pgai_base_path, args.eventid, args.originid, sta_dir.name, stage1_jsons[0], station_string)
                
                if success:
                    continue 
                else:
                    print(f"{C_RED}Zoom generation failed. Skipping to the next station.{C_END}")
                    break 

            elif level == "1b":
                print(f"{C_YELLOW}[Level 1b] Zoom files ready. Stage 2 is missing.{C_END}")
                print(f"\n{C_BLUE}--- PROMPT IN GEMINI (RUN 2) ---{C_END}")
                print(f"{prompt_zoom}")
                print(f"{C_BLUE}--------------------------------{C_END}\n")
                
                # --- Safe automatic copy to clipboard ---
                copy_prompt_to_clipboard(prompt_zoom)
                
                ans = input("\n➡️ Drag the ZOOM images to Gemini along with the prompt.\nType 'y' and press ENTER to open the Stage 2 JSON file: ").strip().lower()
                
                if ans == 'y':
                    stage2_path = sta_dir / f"{sta_dir.name}_stage2.json"
                    stage2_path.touch()
                    open_file_in_editor(stage2_path)
                    
                    input(f"{C_BLUE}➡️ File '{stage2_path.name}' opened! Paste the output, SAVE the file and press ENTER here to continue...{C_END}")
                    
                    if not is_valid_json(stage2_path):
                        print(f"{C_RED}[ERROR] The file is empty or the JSON is invalid! Check that you pasted and saved correctly.{C_END}")
                        continue
                    continue 
                else:
                    print("Input not recognized. Try again.")

    print(f"\n{C_GREEN}All stations in the folder have been processed!{C_END}")

if __name__ == "__main__":
    main()
