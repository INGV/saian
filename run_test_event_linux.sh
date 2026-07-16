#!/bin/bash

# 1. Inizializza Conda nel modo corretto per gli script di shell
# Nota: ho mantenuto il tuo percorso, ma ho aggiunto un controllo standard per Ubuntu
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
read -r eid oid <<< $(echo "$2" | gawk -F\, '{print $1,$2}')
read -r mindist maxdist <<< $(echo "$3" | gawk -F\, '{print $1,$2}')

# 1. Creiamo l'array per gli argomenti opzionali
# In Bash tolgo "local" perché l'allocazione locale è permessa solo dentro le funzioni
opt_args=()

# 2. Controlliamo se $oid NON è vuoto (-n)
if [[ -n "$oid" ]]; then
    # Aggiungiamo sia il flag che il valore come due elementi distinti dell'array
    opt_args+=(--originid "$oid")
fi

# 3. Esegue la logica
if [[ "$action" == "full" ]]; then
        python3 pgai/waves2pgai.py \
                --config ./pgai_config.json \
                --eventid "$eid" \
                "${opt_args[@]}" \
                --networks IV \
                --channels EH,HH \
                --distances "$mindist,$maxdist" \
                --full

elif [[ "$action" == "zoom" ]]; then

        # 1. Chiede l'input all'utente (il flag -n evita di andare a capo)
        echo -n "Inserisci nome della dir di stazione (ex: 0004_IV.APEC.--.HH): "
        read -r nslc_input
        read -r station_string <<< $(echo "$nslc_input" | gawk -F\_ '{print $2}')

        echo -n "Inserisci nome del file json stage (es: 0004_IV.APEC.--.HH_stage1.json): "
        read -r json_input

        # 5. Lancia Python con i parametri dinamici appena creati
        python3 pgai/waves2pgai.py \
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
