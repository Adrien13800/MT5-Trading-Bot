#!/bin/bash
# Supprime le prefixe 0~ parfois ajoute en copier-coller en debut de src/main.rs
# A lancer depuis la racine du projet Rust (dossier qui contient src/)
if [ -f src/main.rs ]; then
  sed -i '1s/^0~//' src/main.rs
  echo "OK: ligne 1 de src/main.rs nettoyee (0~ supprime si present)."
else
  echo "Erreur: src/main.rs introuvable. Lancez ce script depuis la racine du projet (ofi_bot_trading)."
  exit 1
fi
