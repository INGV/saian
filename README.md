# SAIAN

# AI-Assisted Seismic Phase Picking via Visual Waveform Interpretation

## Overview

This project provides a Python tool designed to support a workflow where a generative AI specialized in seismic monitoring analyzes standardized waveform images to detect seismic signals and estimate seismic phase arrivals.

The core idea is to reproduce the visual workflow of an experienced seismic analyst in a seismic monitoring room, allowing the AI to interpret waveform plots rather than raw time series data.

The tool performs waveform acquisition, standardized plotting, and automated zoom generation around candidate seismic phases to enable iterative AI-assisted phase picking.

---

# Workflow

The workflow is structured in two main stages.

## 1. Signal Detection and Preliminary Picking

The tool generates a full waveform plot from seismic data retrieved via FDSN services.

The generative AI receives this image and determines:

- whether a seismic signal is present  
- whether a P phase is visible  
- whether a S phase is visible    
- approximate positions of the phases on the waveform     

The AI returns approximate pick positions measured directly on the full waveform image.

---

## 2. High-Resolution Phase Refinement

Using the preliminary picks returned by the AI, the tool automatically generates high-resolution zoom plots centered on:

- the P phase  
- the S phase  

These zoomed images are then provided back to the AI for fine picking.

At this stage the AI returns:

- refined P pick  
- refined S pick  
- phase visibility (if the phase is not observable)  
- uncertainty estimate (possibly asymmetric)  
- polarity, when determinable

---

# Key Design Principles

The project is based on several principles:

### Visual interpretation instead of raw waveform input

Instead of feeding raw waveform arrays directly into a model, the system presents carefully designed waveform plots that mimic the visual representation used by human analysts.

### Standardized plotting

Plots are generated with controlled properties:

- high resolution  
- consistent time axes  
- precise tick spacing  
- standardized scaling

This ensures both human readability and AI interpretability.

### Iterative refinement

The picking process is intentionally split into two stages:

1. coarse detection on the full waveform  
2. high-precision picking on zoomed views  

This approach mirrors the workflow used in manual seismic analysis.

---

# Features

The current tool provides:

- waveform download via FDSN dataselect  
- metadata caching via FDSN station  
- per-channel MiniSEED export  
- standardized waveform plots  
- automatic P and S zoom generation  
- configurable plotting parameters via JSON

## AI Picks JSON Format

The generative AI used in this project must return its picking results in a structured JSON file that can be used as input by `waves2pgai.py` in `--zoom` mode.

This JSON is intended to represent the output of one AI interpretation step on waveform images.

### Purpose

The JSON file allows the tool to:

- associate AI picks with specific stations
- use AI-generated P and S picks to create high-resolution zoom plots
- optionally carry uncertainty, polarity, and suggested preprocessing parameters

### General structure

The JSON file contains:

- an optional `event` section
- a mandatory `stations` array
- one object per station analyzed by the AI

An example is reported at the bottom of the instruction.txt file in this repository.

---

# Execution Workflow Instructions

The following operational guidelines detail the setup and execution phases required to run the SAIAN pipeline, as extracted and translated from the reference document **SAIAN_Workflow.pdf**.

## 1. System Requirements & Setup

### Git Repository Setup
1. Clone the repository into your local environment:
   ```bash
   git clone git@github.com:INGV/saian.git
   ```
2. Create a dedicated working directory in your preferred location (e.g., `saian_working_dir`).
3. Copy the `saian_config.json` configuration file from the Git repository into your working directory.
4. Within the working directory, create a text file named `path_to_git_saian.txt` containing the absolute path to your local Git repository.

### Python Environment Validation
- **Interpreter:** Python 3.12 or a library-compatible environment is strictly required.
- The pipeline leverages a `requirements.py` script (which reads the `path_to_git_saian.txt` file) to validate that all required dependencies are present in your Python environment.
- **Best Practice:** It is highly recommended to instantiate an isolated virtual environment (e.g., via `conda`), install the necessary packages within it, and execute all subsequent pipeline commands from this environment.

### GEM SAIAN Instantiation
1. Access the Gemini web interface and navigate to the **Gems** section via the sidebar.
2. Create a new Gem. Naming it **SAIAN** is optional but highly recommended for workflow clarity.
3. Set the **Description** parameter to: `Makes picks on waveforms`.
4. In the **Instructions** configuration block, paste the entire contents of the `instructions.txt` file found in the cloned Git repository.
5. Save the Gem configuration.
6. Invoke the newly configured GEM and record its unique alphanumeric identifier from the browser URL (e.g., if the URL is `https://gemini.google.com/gem/873ddab7290d`, the GEM ID is `873ddab7290d`).

---

## 2. Pipeline Execution Operations

### Event Selection
By default, the `saian_config.json` file is configured to interface with the INGV web services.
- Select a target event from the INGV earthquake database (accessible via `https://terremoti.ingv.it` or subsequent endpoints).
- Retrieve and copy the **Event ID**. Note that supplying an **Origin ID** is strictly optional; if omitted, the scripts will automatically default to the "preferred" origin metadata.

### STEP 1: Initial Waveform Extraction (`run full cut`)
1. Open a terminal session and ensure your Python virtual environment is active.
2. Navigate to your designated working directory.
3. Execute the shell script using the following syntax:
   ```bash
   /path_to_saian_repo/saian_run.sh [eventid] [originid] 0 100
   ```
**Execution Subroutines:**
- The script extracts a list of stations belonging to the `IV` network *(Note: The script currently hardcodes the network variable, as well as the `HH` and `EH` channels, passing them via the `--networks` and `--channels` arguments to the underlying Python backend)*.
- It filters for stations located within an epicentral distance range of 0 to 100 km (handled via the `--distances` flag).
- It provisions a dedicated event directory named `waveforms_event_eid[eventid]_oid[originid]` and creates station-specific subdirectories within it.
- Waveforms are temporally sliced starting 5 seconds prior to the theoretical P-wave arrival and ending 20 seconds after the theoretical S-wave arrival (as dictated by the `saian_config.json` parameters).
- **Outputs generated per station:**
  - A `.png` image representing the "full cut", featuring the three seismic components stacked vertically.
  - The corresponding raw `.mseed` data files.
  - Extracted JSON metadata files.

### STEP 2: Automated AI Processing
*Important: Utilizing the Event ID and Origin ID precisely as they appear in the directory name generated during STEP 1 is mandatory for this execution phase.*

Initiate the master automation controller:
```bash
python3 /path_to_SAIAN_git_dir/saian_auto_runner.py --eventid [eventid] --originid [originid] --gemid [GEM_ID]
```

**Operational Logic and User Interaction:**
From this point forward, the operational logic is strictly driven by the Python controller. It parses the event directory based on the provided Event ID and Origin ID, iterates through the station subdirectories, verifies their current processing state, and automatically resumes operations from the last completed checkpoint. The script will prompt the user when manual interaction is required.

- **Initialization:** Upon execution, the script will output the number of identified stations and ask: `Do you want to automatically skip already completed stations (Level 2)? (y/n):`. Typing `y` bypasses completed nodes and halts at the first incomplete station; typing `n` presents them sequentially for manual skipping.
- **Browser Setup Requirement:** Prior to processing, ensure you have an independent browser window open, separate from tabs running other active Gemini sessions. Keep this window visible alongside your terminal. **Always ensure that the active Gemini model is set to the "Reasoning" tier**, as browser sessions may cache settings if "Pro" or "Flash" models were active in adjacent tabs. Read terminal prompts carefully to navigate the multithreaded logic correctly.

**Stage 1: Preliminary Phase Detection**
1. The script will instantiate the SAIAN GEM in a new browser tab. *(Caution: The script opens new tabs but does not close legacy tabs; the user must manage tab cleanup manually).*
2. Ensure you are authenticated in the active browser session with an account authorized to access the designated GEM.
3. The required prompt text is automatically copied to your system clipboard (or available in the terminal stdout as a fallback).
4. Drag and drop the **FULL waveform image** into the Gemini interface, paste the copied prompt, and execute the query.
5. When the GEM generates the JSON response, return to the terminal and type `y`, then press `ENTER`.
6. The script will automatically create the `stage1.json` file in the correct directory path and open it in your system's default text editor.
7. Copy the JSON code block from Gemini, paste it into the opened text editor, save the file, and close it.
8. Press `ENTER` in the terminal to acknowledge completion. The controller will evaluate the JSON payload and automatically invoke `waves2saian.py` with the `--zoom` flag to render high-resolution context subsets based on the Stage 1 picks.

**Stage 2: High-Resolution Phase Refinement**
1. Once the zoom processing successfully completes, the script copies the Stage 2 instruction prompt to your clipboard and notifies you that the zoom files are ready.
2. Drag and drop the newly generated **ZOOM waveform images** into the *same* Gemini instance, paste the Stage 2 prompt, and execute the query.
3. Return to the terminal, type `y`, and press `ENTER`. The script will open the `stage2.json` file.
4. Copy the refined JSON output from Gemini, ensuring no extraneous markdown or characters are included, paste it into the editor, save, and close.
5. Press `ENTER` in the terminal. The script will log `[Level 2] Processing completed (Stage 2 JSON present)` and prompt: `Move on to the next station? (y/n):`.
6. Pressing `y` instructs the controller to proceed, which will open a new GEM instance in a new tab for the next station. Wait for the new tab to instantiate fully before closing the previous one; accidental closure of the active tab requires manual reopening.

**Critical Stability Warnings:**
- You may halt the process at any time by terminating the script.
- **Never leave empty JSON files in the workspace directories.** Saving a blank JSON file will trigger fatal parsing errors in the `waves2saian` dependency, causing the master script to fail and forcing a manual rollback of the file state.
