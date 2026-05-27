"""LoRA fine-tune a small open-weight model on the CN training set.

Runs on Modal cloud (modal.com). Uses Qwen 2.5 7B Instruct as the base
because it's the strongest small model right now and fits in 24GB VRAM
with LoRA + 4-bit quantization.

Run from your laptop after `modal token new`:
    modal run scripts/finetune_modal.py

This launches a remote A10G GPU, syncs the training data, runs LoRA
training (~1-2h depending on epochs), and writes the adapter weights
back to the Modal volume so subsequent inference runs can load it
without re-uploading. Expected cost: $5-15.

When training finishes, see `scripts/serve_finetune_modal.py` for the
inference endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import modal

APP_NAME = "cn-bot-finetune"
VOLUME_NAME = "cn-bot-models"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TRAINING_PATH = REPO_ROOT / "data" / "cn_training.jsonl"


# Modal image. PyTorch + transformers + peft + trl + datasets, plus
# bitsandbytes for 4-bit quantization so we fit on A10G (24GB).
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install(
        "torch==2.4.0",
        "transformers==4.46.0",
        "peft==0.13.0",
        "trl==0.11.0",
        "datasets==3.0.0",
        "accelerate==1.0.0",
        "bitsandbytes==0.44.0",
        "sentencepiece",
        "protobuf",
        # TRL imports rich for its training progress UI; not pulled in by default
        "rich",
        # SFTTrainer needs tensorboard or wandb installed for logging when
        # report_to is anything other than "none"
        "tensorboard",
    )
)

app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


# Prompt template — same shape we'll use at inference time.
# The model learns to map (TWEET) → (HELPFUL NOTE).
SYSTEM_PROMPT = (
    "You write Community Notes for X. Given a post, produce a helpful note "
    "in the style raters approve: direct factual correction, named source, "
    "tight prose, ends with a citation URL. Match what an X reader would "
    "rate Currently Rated Helpful."
)


def _format_example(record: dict) -> dict[str, str]:
    """Turn a (tweet, helpful_note) row into a chat-format training example."""
    tweet = record["tweet"]["tweet_text"]
    note = record["note"]["text"].strip()
    return {
        "prompt": f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\nX post:\n{tweet}\n\nWrite the Community Note.<|im_end|>\n<|im_start|>assistant\n",
        "completion": f"{note}<|im_end|>",
    }


@app.function(
    gpu="A10G",
    timeout=60 * 60 * 4,  # 4h max
    volumes={"/models": volume},
)
def train(training_data: list[dict], epochs: int = 3, lr: float = 2e-4) -> str:
    """LoRA-tune Qwen 2.5 7B on the supplied training data. Writes adapter to volume."""
    import os
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainingArguments,
    )
    from trl import SFTTrainer

    print(f"Training on {len(training_data)} examples")

    # Filter to helpful examples only — we want the model to imitate CRH notes.
    helpful = [r for r in training_data if r.get("label") == "helpful"]
    print(f"  helpful examples: {len(helpful)}")

    formatted = [_format_example(r) for r in helpful]
    ds = Dataset.from_list(formatted)
    ds = ds.shuffle(seed=42)
    split = ds.train_test_split(test_size=0.05, seed=42)

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    out_dir = "/models/cn-bot-qwen25-7b-lora"
    args = TrainingArguments(
        output_dir=out_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=lr,
        bf16=True,
        logging_steps=20,
        save_strategy="epoch",
        eval_strategy="epoch",
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=split["train"],
        eval_dataset=split["test"],
        args=args,
        tokenizer=tokenizer,
        max_seq_length=2048,
        dataset_text_field=None,  # we use prompt+completion explicitly
        formatting_func=lambda ex: ex["prompt"] + ex["completion"],
    )

    print("Starting training")
    trainer.train()

    print(f"Saving adapter to {out_dir}")
    trainer.save_model(out_dir)
    tokenizer.save_pretrained(out_dir)
    volume.commit()
    return out_dir


@app.local_entrypoint()
def main():
    if not LOCAL_TRAINING_PATH.exists():
        raise SystemExit(f"No training data at {LOCAL_TRAINING_PATH}")

    with LOCAL_TRAINING_PATH.open() as f:
        records = [json.loads(line) for line in f if line.strip()]
    print(f"Loaded {len(records)} records locally; sending to Modal")
    out = train.remote(records)
    print(f"Adapter written to volume at: {out}")
