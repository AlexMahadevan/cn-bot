"""LoRA fine-tune Qwen 2.5 7B on (tweet + evidence → note) triples.

Same Modal infrastructure as finetune_modal.py — same image, same volume,
same A10G GPU — but reads data/cn_training_v2.jsonl and uses a different
prompt template that includes the article body the note cites.

The v1 fine-tune (finetune_modal.py) trained on (tweet → note) pairs.
At inference time we tried to inject evidence the model had never seen
in training, and got out-of-distribution behavior — the model partially
copied the evidence, partially confabulated. This version teaches the
model to use injected context from the start.

Run from your laptop:
    modal run --detach scripts/finetune_modal_v2.py
"""

from __future__ import annotations

import json
from pathlib import Path

import modal

APP_NAME = "cn-bot-finetune-v2"
VOLUME_NAME = "cn-bot-models"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TRAINING_PATH = REPO_ROOT / "data" / "cn_training_v2.jsonl"

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
        "rich",
        "tensorboard",
    )
)

app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


SYSTEM_PROMPT = (
    "You write Community Notes for X. Given a post and a background "
    "article that's relevant to the post, produce a helpful note in the "
    "style raters approve: direct factual correction, named source, tight "
    "prose. Paraphrase the article to ground the note in real facts — do "
    "not invent details beyond what the article says. End with a citation URL."
)


def _format_example(record: dict) -> dict[str, str]:
    """(tweet, article, note) → chat-template prompt/completion pair."""
    tweet = record["tweet"]["tweet_text"]
    article = record["evidence"]["text"]
    note = record["note"]["text"].strip()

    user_block = (
        f"Background article (use as ground truth, paraphrase only):\n"
        f"{article.strip()}\n\n"
        f"X post:\n{tweet}\n\n"
        f"Write the Community Note."
    )
    return {
        "prompt": (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{user_block}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        ),
        "completion": f"{note}<|im_end|>",
    }


@app.function(
    gpu="A10G",
    timeout=60 * 60 * 4,
    volumes={"/models": volume},
)
def train(training_data: list[dict], epochs: int = 3, lr: float = 2e-4) -> str:
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

    print(f"Training on {len(training_data)} triples")
    formatted = [_format_example(r) for r in training_data]
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

    out_dir = "/models/cn-bot-qwen25-7b-lora-v2"
    args = TrainingArguments(
        output_dir=out_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=8,
        gradient_checkpointing=True,
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
        # Longer max_seq_length than v1 because evidence text adds ~1000 chars
        max_seq_length=2048,
        dataset_text_field=None,
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
        raise SystemExit(f"No v2 training data at {LOCAL_TRAINING_PATH}. Run build_training_set_v2.py first.")
    with LOCAL_TRAINING_PATH.open() as f:
        records = [json.loads(line) for line in f if line.strip()]
    print(f"Loaded {len(records)} triples locally; sending to Modal")
    out = train.remote(records)
    print(f"Adapter written to volume at: {out}")
