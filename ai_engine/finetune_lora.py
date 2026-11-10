"""Fine-tuning éco-responsable de NLLB (Français -> Lingala) avec LoRA/PEFT (Sprint 3).

- Modèle open-source : facebook/nllb-200-distilled-600M (traducteur encodeur-décodeur, 200 langues)
- Poids d'origine GELÉS ; seules de petites matrices LoRA (rang 8) sont entraînées,
  sur les couches d'attention q_proj et v_proj (environ 0,2 % des paramètres)
- Données : le dataset sans fuite de ai_engine/prepare_dataset.py (train / validation / test)
- Évaluation scientifique : SacreBLEU sur le jeu de TEST via Seq2SeqTrainer, AVANT (modèle
  d'origine) et APRÈS (modèle + LoRA), pour mesurer l'apport réel du fine-tuning

À lancer sur une machine avec GPU (par exemple RunPod), après :
    pip install -r requirements-train.txt
    python -m ai_engine.finetune_lora --essai   # 200 phrases, 1 époque : vérifie le script (~3 min)
    python -m ai_engine.finetune_lora           # entraînement complet (~25 min sur une RTX A5000)

Sorties : ai_engine/modele_lora/ (adaptateur LoRA, ~5 Mo) + ai_engine/modele_lora/resultats.json
"""
import argparse
import json
import os
import time

import evaluate
import numpy as np
import torch
from datasets import load_dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOSSIER_DONNEES = os.path.join(BASE_DIR, "ai_engine", "data")
DOSSIER_SORTIE = os.path.join(BASE_DIR, "ai_engine", "modele_lora")

MODELE = "facebook/nllb-200-distilled-600M"
LANGUE_SOURCE, LANGUE_CIBLE = "fra_Latn", "lin_Latn"  # codes de langue NLLB
LONGUEUR_MAX = 128   # tokens par phrase
GRAINE = 42
TAILLE_ESSAI = 200   # phrases par sous-ensemble en mode --essai

# Hyperparamètres LoRA (ceux de l'article LoRA et du module 3)
LORA = {"r": 8, "lora_alpha": 16, "lora_dropout": 0.05, "target_modules": ["q_proj", "v_proj"]}


def charger_donnees(essai):
    fichiers = {nom: os.path.join(DOSSIER_DONNEES, f"{nom}.jsonl") for nom in ("train", "validation", "test")}
    manquants = [f for f in fichiers.values() if not os.path.exists(f)]
    if manquants:
        raise SystemExit("Dataset absent : lancez d'abord  python -m ai_engine.prepare_dataset")
    donnees = load_dataset("json", data_files=fichiers)
    if essai:
        for nom in donnees:
            donnees[nom] = donnees[nom].select(range(min(TAILLE_ESSAI, len(donnees[nom]))))
    return donnees


def tokeniser(donnees, tokenizer):
    def preparer(lot):
        entrees = tokenizer(lot["fr"], max_length=LONGUEUR_MAX, truncation=True)
        entrees["labels"] = tokenizer(text_target=lot["ln"], max_length=LONGUEUR_MAX, truncation=True)["input_ids"]
        return entrees

    return donnees.map(preparer, batched=True, remove_columns=donnees["train"].column_names)


def creer_calcul_bleu(tokenizer):
    """Fonction appelée par Seq2SeqTrainer : décode les traductions générées et calcule SacreBLEU."""
    sacrebleu = evaluate.load("sacrebleu")

    def calculer(eval_preds):
        predictions, labels = eval_preds
        if isinstance(predictions, tuple):
            predictions = predictions[0]
        # -100 = position ignorée (remplissage) : on la remplace par le jeton de remplissage avant décodage
        predictions = np.where(predictions != -100, predictions, tokenizer.pad_token_id)
        labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
        traductions = [t.strip() for t in tokenizer.batch_decode(predictions, skip_special_tokens=True)]
        references = [[t.strip()] for t in tokenizer.batch_decode(labels, skip_special_tokens=True)]
        return {"bleu": round(sacrebleu.compute(predictions=traductions, references=references)["score"], 2)}

    return calculer


def arguments_entrainement(dossier, essai):
    return Seq2SeqTrainingArguments(
        output_dir=os.path.join(dossier, "checkpoints"),
        eval_strategy="epoch",
        save_strategy="epoch",
        learning_rate=5e-4,                 # plus élevé qu'un fine-tuning complet : LoRA a peu de paramètres
        per_device_train_batch_size=16,
        per_device_eval_batch_size=32,
        num_train_epochs=1 if essai else 3,
        weight_decay=0.01,
        predict_with_generate=True,         # l'évaluation TRADUIT vraiment les phrases (nécessaire au BLEU)
        generation_max_length=LONGUEUR_MAX,
        generation_num_beams=4,
        load_best_model_at_end=True,        # on garde l'époque qui a le meilleur BLEU en validation
        metric_for_best_model="bleu",
        greater_is_better=True,
        save_total_limit=1,
        logging_steps=50,
        bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        report_to="none",
        seed=GRAINE,
    )


def traduire(modele, tokenizer, phrases):
    entrees = tokenizer(phrases, return_tensors="pt", padding=True, truncation=True,
                        max_length=LONGUEUR_MAX).to(modele.device)
    with torch.no_grad():
        sorties = modele.generate(**entrees, max_length=LONGUEUR_MAX, num_beams=4)
    return tokenizer.batch_decode(sorties, skip_special_tokens=True)


def main(essai):
    debut = time.time()
    dossier = DOSSIER_SORTIE + ("_essai" if essai else "")
    print(f"GPU disponible : {torch.cuda.is_available()}"
          + (f" ({torch.cuda.get_device_name(0)})" if torch.cuda.is_available() else " -> ce sera très lent"))

    donnees = charger_donnees(essai)
    print(donnees)
    tokenizer = AutoTokenizer.from_pretrained(MODELE, src_lang=LANGUE_SOURCE, tgt_lang=LANGUE_CIBLE)
    modele = AutoModelForSeq2SeqLM.from_pretrained(MODELE)
    modele.generation_config.forced_bos_token_id = tokenizer.convert_tokens_to_ids(LANGUE_CIBLE)
    donnees_tok = tokeniser(donnees, tokenizer)
    exemples = donnees["test"]["fr"][:5]

    # ---- 1. SacreBLEU du modèle d'ORIGINE (référence) sur le jeu de test ----
    evaluateur = Seq2SeqTrainer(
        model=modele, args=arguments_entrainement(dossier, essai), processing_class=tokenizer,
        data_collator=DataCollatorForSeq2Seq(tokenizer, model=modele),
        compute_metrics=creer_calcul_bleu(tokenizer),
    )
    bleu_avant = evaluateur.evaluate(donnees_tok["test"], metric_key_prefix="test")["test_bleu"]
    print(f"\n[avant fine-tuning] SacreBLEU test = {bleu_avant}")
    traductions_avant = traduire(modele, tokenizer, exemples)

    # ---- 2. Geler les poids d'origine et ajouter LoRA sur q_proj et v_proj ----
    for parametre in modele.parameters():
        parametre.requires_grad = False
    modele = get_peft_model(modele, LoraConfig(task_type=TaskType.SEQ_2_SEQ_LM, **LORA))
    entrainables, total = modele.get_nb_trainable_parameters()
    print(f"Paramètres entraînables : {entrainables:,} sur {total:,} ({100 * entrainables / total:.2f} %)")

    # ---- 3. Entraînement ----
    trainer = Seq2SeqTrainer(
        model=modele,
        args=arguments_entrainement(dossier, essai),
        train_dataset=donnees_tok["train"],
        eval_dataset=donnees_tok["validation"],
        processing_class=tokenizer,
        data_collator=DataCollatorForSeq2Seq(tokenizer, model=modele),
        compute_metrics=creer_calcul_bleu(tokenizer),
    )
    trainer.train()

    # ---- 4. SacreBLEU APRÈS fine-tuning, sur le même jeu de test ----
    resultats_test = trainer.evaluate(donnees_tok["test"], metric_key_prefix="test")
    bleu_apres = resultats_test["test_bleu"]
    print(f"\n[après fine-tuning]  SacreBLEU test = {bleu_apres}  (avant : {bleu_avant})")
    traductions_apres = traduire(trainer.model, tokenizer, exemples)

    # ---- 5. Sauvegarde : adaptateur LoRA (quelques Mo) + résultats ----
    trainer.model.save_pretrained(dossier)
    resultats = {
        "modele_de_base": MODELE,
        "langues": f"{LANGUE_SOURCE} -> {LANGUE_CIBLE}",
        "lora": LORA,
        "parametres_entrainables": entrainables,
        "parametres_total": total,
        "pourcentage_entraine": round(100 * entrainables / total, 3),
        "taille_dataset": {nom: len(ds) for nom, ds in donnees.items()},
        "epoques": arguments_entrainement(dossier, essai).num_train_epochs,
        "sacrebleu_test_avant": bleu_avant,
        "sacrebleu_test_apres": bleu_apres,
        "gain_bleu": round(bleu_apres - bleu_avant, 2),
        "exemples": [{"fr": fr, "avant": a, "apres": b, "reference": ref}
                     for fr, a, b, ref in zip(exemples, traductions_avant, traductions_apres, donnees["test"]["ln"][:5])],
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "duree_minutes": round((time.time() - debut) / 60, 1),
        "mode_essai": essai,
    }
    with open(os.path.join(dossier, "resultats.json"), "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)
    print(f"Adaptateur et résultats enregistrés dans {os.path.relpath(dossier, BASE_DIR)}/ "
          f"({resultats['duree_minutes']} min)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fine-tuning LoRA de NLLB (français -> lingala)")
    parser.add_argument("--essai", action="store_true", help=f"{TAILLE_ESSAI} phrases et 1 époque, pour tester")
    main(parser.parse_args().essai)
