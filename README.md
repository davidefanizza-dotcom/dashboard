# Buongiorno — dashboard di inizio giornata

Meteo (Open-Meteo), attività e promemoria in un unico file HTML, stile iOS.

Nella card Meteo si può passare alle previsioni di **meteo.it** e **iLMeteo**, disegnate con le stesse card: i dati li scarica ogni ora una GitHub Action (`.github/workflows/meteo.yml` → `scripts/scrape_meteo.py`) e li salva in `data/meteo-<città>.json`. Le città seguite sono nell'elenco `CITIES` dello script (per ora Calimera).
Sincronizzazione tra dispositivi tramite Gist GitHub (token con permesso `gist`).

Live: https://davidefanizza-dotcom.github.io/dashboard/

I video e i poster dei cieli in `sky/` provengono da Mixkit: dettagli in [sky/CREDITS.md](sky/CREDITS.md).
