#! /bin/zsh

# 1. Inizializza Conda nel modo corretto per gli script di shell
source /opt/anaconda3/etc/profile.d/conda.sh

# 2. Attiva l'ambiente una sola volta per tutto lo script
conda activate /opt/anaconda3/envs/seismo_clean

if [[ $# -lt 3 ]]; then
    echo ""
    echo "Usage: $0 [action: full or zoom] [eventid,originid] [mindist,maxdist]"
    echo ""
    exit
fi

action=$(echo $1 | tr "[:upper:]" "[:lower:]")
read -r eid oid <<< $(echo $2 | gawk -F\, '{print $1,$2}')
read -r mindist maxdist <<< $(echo $3 | gawk -F\, '{print $1,$2}')

# --- NUOVA LOGICA: RISOLUZIONE DINAMICA DEL PATH PGAI ---
PGAI_DIR="./pgai" # Fallback di default
CONFIG_FILE="path_to_git_pgai.txt"

if [[ -f "$CONFIG_FILE" ]]; then
    # Legge la prima riga del file
    read -r custom_path < "$CONFIG_FILE"
    
    # Rimuove eventuali spazi vuoti iniziali e finali (trim)
    custom_path=$(echo "$custom_path" | xargs)
    
    # Sostituisce in modo sicuro la tilde (~) con la variabile d'ambiente $HOME
    PGAI_DIR="${custom_path/#\~/$HOME}"
fi

# Costruiamo il percorso assoluto allo script Python
WAVES_SCRIPT="${PGAI_DIR}/waves2pgai.py"

# Verifica di sicurezza prima di lanciare processi a vuoto
if [[ ! -f "$WAVES_SCRIPT" ]]; then
    echo -e "\n[ERRORE] Lo script non è stato trovato in: $WAVES_SCRIPT"
    echo "Controlla il contenuto di $CONFIG_FILE o assicurati che esista la dir ./pgai."
    exit 1
fi
# --------------------------------------------------------

# Creiamo un array (inizialmente vuoto) per gli argomenti opzionali
local opt_args=()

# Controlliamo se $oid NON è vuoto (-n)
if [[ -n "$oid" ]]; then
    # Aggiungiamo sia il flag che il valore come due elementi distinti dell'array
    opt_args+=(--originid "$oid")
fi

# Esegue la logica
if [[ $action == "full" ]]; then
        python3 "$WAVES_SCRIPT" \
                --config ./pgai_config.json \
                --eventid "$eid" \
                "${opt_args[@]}" \
                --networks IV \
                --channels EH,HH \
                --distances "$mindist,$maxdist" \
                --full
                
elif [[ $action == "zoom" ]]; then

        # Chiede l'input all'utente
        echo -n "Inserisci nome della dir di stazione (ex: 0004_IV.APEC.--.HH): "
        read nslc_input
        read station_string <<< $(echo $nslc_input | gawk -F\_ '{print $2}')

        echo -n "Inserisci nome del file json stage (es: 0004_IV.APEC.--.HH_stage1.json): "
        read json_input
        
        # Lancia Python con il percorso dinamico
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
