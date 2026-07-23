#!/bin/bash

# 1. Inizializza Conda nel modo corretto per gli script di shell
if [ -f /opt/anaconda3/etc/profile.d/conda.sh ]; then
    source /opt/anaconda3/etc/profile.d/conda.sh
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
else
    echo "Errore: Impossibile trovare conda.sh. Verifica il percorso di installazione di Conda."
    exit 1
fi

# 2. Attiva l'ambiente 'pgai' per tutto lo script
conda activate pgai

# Controllo argomenti inseriti
if [[ $# -lt 3 ]]; then
        echo ""
        echo "Usage: $0 [action: full or zoom] [eventid,originid] [mindist,maxdist]"
        echo ""
        exit 1
fi

action=$(echo "$1" | tr "[:upper:]" "[:lower:]")

# --- OTTIMIZZAZIONE EXTREME BASH (Senza Gawk) ---
# Usiamo IFS per dividere le stringhe istantaneamente in RAM
IFS=, read -r eid oid <<< "$2"
IFS=, read -r mindist maxdist <<< "$3"
# ------------------------------------------------

# --- NUOVA LOGICA: RISOLUZIONE DINAMICA DEL PATH PGAI ---
PGAI_DIR="./pgai" # Fallback di default
CONFIG_FILE="path_to_git_pgai.txt"

if [[ -f "$CONFIG_FILE" ]]; then
    # Legge la prima riga del file testuale
    read -r custom_path < "$CONFIG_FILE"
    
    # Trim degli spazi
    custom_path=$(echo "$custom_path" | xargs)
    
    # Traduzione sicura della tilde (~) nel percorso home assoluto
    PGAI_DIR="${custom_path/#\~/$HOME}"
fi

# Costruiamo il percorso sicuro ed esplicito
WAVES_SCRIPT="${PGAI_DIR}/waves2pgai.py"

if [[ ! -f "$WAVES_SCRIPT" ]]; then
    echo -e "\n[ERRORE] Lo script non è stato trovato in: $WAVES_SCRIPT"
    echo "Controlla il contenuto di $CONFIG_FILE o assicurati che esista la dir ./pgai."
    exit 1
fi
# --------------------------------------------------------

# Creiamo l'array per gli argomenti opzionali
opt_args=()

# Controlliamo se $oid NON è vuoto (-n)
if [[ -n "$oid" ]]; then
    opt_args+=(--originid "$oid")
fi

# Esegue la logica
if [[ "$action" == "full" ]]; then
        python3 "$WAVES_SCRIPT" \
                --config ./pgai_config.json \
                --eventid "$eid" \
                "${opt_args[@]}" \
                --networks IV \
                --channels EH,HH \
                --distances "$mindist,$maxdist" \
                --full

elif [[ "$action" == "zoom" ]]; then

        echo -n "Inserisci nome della dir di stazione (ex: 0004_IV.APEC.--.HH): "
        read -r nslc_input
        
        # Altra ottimizzazione senza gawk: divide sull'underscore
        IFS=_ read -r _ station_string <<< "$nslc_input"

        echo -n "Inserisci nome del file json stage (es: 0004_IV.APEC.--.HH_stage1.json): "
        read -r json_input

        # Lancia Python richiamando il Path risolto
        python3 "$WAVES_SCRIPT" \
                --config ./pgai_config.json \
                --eventid "$eid" \
                "${opt_args[@]}" \
                --ai-picks-json "waveforms_event_eid${eid}_oid${oid}/${nslc_input}/${json_input}" \
                --zoom \
                --zoom-levels context \
                --stations "$station_string" \
                --expand-dynamics \
                --filter suggested
else
        echo "Argomento non valido. Usa 'full' o 'zoom'."
        exit 1
fi
