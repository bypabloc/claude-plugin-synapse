#!/usr/bin/env python3
"""Ajuste fino parcial de Laya para block_dangerous (paso 4: dominio propio).

Entrena solo las últimas capas del encoder ModernBERT-large y la cabeza de decisión (`scorer`,
`type_emb`, `final_norm`) con cross-entropy sobre la pregunta binaria de laya_eval (Q_BIN), usando el
split 'fit' del dataset. El resultado es un delta de pesos que se aplica encima del checkpoint público:

  $ python3 scripts/laya_finetune.py train --input evid --epochs 2 --out tmp/eval/ft_delta.safetensors
  $ python3 scripts/laya_eval.py score evid-bin@ft   # con LAYA_FT_DELTA apuntando al delta

Un ajuste completo (421M parámetros + AdamW) no cabe en 8 GB de VRAM; las capas superiores sí.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_eval as ev  # noqa: E402
from laya.common import collate_items  # noqa: E402

TRAINABLE_PREFIXES = ("scorer.", "type_emb.", "encoder.final_norm.")


def trainable(name: str, top_layers: int, n_layers: int) -> bool:
    if name.startswith(TRAINABLE_PREFIXES):
        return True
    parts = name.split(".")
    return parts[:2] == ["encoder", "layers"] and int(parts[2]) >= n_layers - top_layers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train"])
    ap.add_argument("--input", default="evid", choices=sorted(ev.INPUTS))
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--top-layers", type=int, default=2)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--pos-weight", type=float, default=3.0)
    ap.add_argument("--out", default=str(ev.EVAL_DIR / "ft_delta.pt"))
    args = ap.parse_args()

    router = ev.bd.get_laya_router()
    agent = router.load("english")
    model = agent.model
    n_layers = 1 + max(int(n.split(".")[2]) for n, _ in model.named_parameters() if n.startswith("encoder.layers."))
    names = {n for n, _ in model.named_parameters() if trainable(n, args.top_layers, n_layers)}
    for n, p in model.named_parameters():
        p.requires_grad_(n in names)
    params = [p for n, p in model.named_parameters() if n in names]
    print(f"entrenables: {sum(p.numel() for p in params) / 1e6:.1f}M de {sum(p.numel() for p in model.parameters()) / 1e6:.1f}M")

    qid, qdef = next(iter(ev.Q_BIN.items()))
    internal = {qid: agent._to_internal(qdef)}
    options = list(qdef["criteria"])  # orden de los marcadores = orden de los criterios
    build = ev.INPUTS[args.input]
    rows = [r for r in ev.load_dataset() if r["split"] == "fit" and not r["regex"]]
    examples = []
    for r in rows:
        item = agent._encode_state(build(r["command"])[:4000], [qid], internal)[0]
        item["label"] = options.index("A" if r["label"] else "B")
        examples.append(item)
    print(f"{len(examples)} ejemplos ({sum(r['label'] for r in rows)} riesgosos)")

    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
    class_w = torch.tensor([args.pos_weight, 1.0], device=agent.device)  # índice 0 = 'A' = riesgoso
    model.train()
    rng = random.Random(0)
    for epoch in range(args.epochs):
        rng.shuffle(examples)
        total = 0.0
        for i in range(0, len(examples), args.batch):
            b = collate_items([examples[i:i + args.batch]], agent.tok.pad_token_id)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits, _ = model(
                    b["input_ids"].to(agent.device), b["attention_mask"].to(agent.device),
                    b["marker_pos"].to(agent.device), b["marker_mask"].to(agent.device), b["qtype"].to(agent.device),
                )
            loss = F.cross_entropy(logits[:, :2].float(), b["label"].to(agent.device), weight=class_w)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            total += loss.item()
            if (i // args.batch) % 200 == 0:
                print(f"epoch {epoch} paso {i // args.batch} loss {loss.item():.4f}", flush=True)
        print(f"epoch {epoch} loss medio {total / max(1, len(examples) // args.batch):.4f}", flush=True)

    model.eval()
    delta = {n: p.detach().to(torch.bfloat16).cpu() for n, p in model.named_parameters() if n in names}
    torch.save({"delta": delta, "options": options, "input": args.input, "question": ev.Q_BIN}, args.out)
    print(f"delta guardado en {args.out}")


if __name__ == "__main__":
    main()
