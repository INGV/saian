#! /bin/zsh

# 1. Inizializza Conda nel modo corretto per gli script di shell
source /opt/anaconda3/etc/profile.d/conda.sh

# 2. Attiva l'ambiente una sola volta per tutto lo script
conda activate /opt/anaconda3/envs/seismo_clean

# 3. Esegue la logica
if [[ $1 == "full" ]]; then
	python3 pgai/waves2pgai.py \
		--config ./pgai_config.json \
		--eventid 46057512 \
		--originid 145016481 \
		--networks IV \
		--channels EH,HH \
		--distances 0,200 \
		--full 
elif [[ $1 == "zoom" ]]; then
	python3 pgai/waves2pgai.py \
	    	--config ./pgai_config.json \
	    	--eventid 46057512 \
	    	--originid 145016481 \
	    	--ai-picks-json waveforms_event_eid46057512_oid145016481/IV.SNTG.--.HH/pgaigem_sntg_stage1.json \
	    	--zoom \
	    	--zoom-levels context \
	    	--stations IV.SNTG.--.HH \
	    	--expand-dynamics \
	    	--filter suggested
else
        echo "Argomento non valido. Usa 'full' o 'zoom'."
        exit 1
fi
