"""Inference endpoint for the v2 fine-tuned model (trained on triples).

Same shape as serve_finetune_modal.py — POST /generate, accepts
post_text + evidence_text + max_chars — but loads the v2 adapter
that was trained on (tweet + evidence → note) triples.

Deploy with:
    modal deploy scripts/serve_finetune_modal_v2.py

This creates a separate Modal app (cn-bot-inference-v2) at a distinct
URL so we can A/B against v1 cleanly.
"""

from __future__ import annotations

import modal
from pydantic import BaseModel

APP_NAME = "cn-bot-inference-v2"
VOLUME_NAME = "cn-bot-models"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
ADAPTER_PATH = "/models/cn-bot-qwen25-7b-lora-v2"

SYSTEM_PROMPT = (
    "You write Community Notes for X. Given a post and a background "
    "article that's relevant to the post, produce a helpful note in the "
    "style raters approve: direct factual correction, named source, tight "
    "prose. Paraphrase the article to ground the note in real facts — do "
    "not invent details beyond what the article says. End with a citation URL."
)


def _download_base_model():
    from transformers import AutoModelForCausalLM, AutoTokenizer
    AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype="auto",
        trust_remote_code=True,
    )


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.4.0",
        "transformers==4.46.0",
        "peft==0.13.0",
        "accelerate==1.0.0",
        "bitsandbytes==0.44.0",
        "fastapi[standard]",
        "sentencepiece",
        "protobuf",
    )
    .run_function(_download_base_model)
)

app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name(VOLUME_NAME)


class GenerateRequest(BaseModel):
    post_text: str
    evidence_text: str | None = None
    max_chars: int | None = None
    max_new_tokens: int = 220
    temperature: float = 0.4


class GenerateResponse(BaseModel):
    note_text: str


@app.cls(
    gpu="A10G",
    volumes={"/models": volume},
    scaledown_window=300,
    timeout=120,
)
class FinetunedNoteWriter:
    @modal.enter()
    def load_model(self):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        )
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
        base = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            quantization_config=bnb,
            device_map="auto",
            trust_remote_code=True,
        )
        self.model = PeftModel.from_pretrained(base, ADAPTER_PATH)
        self.model.eval()
        print(f"loaded {BASE_MODEL} + v2 adapter at {ADAPTER_PATH}")

    @modal.fastapi_endpoint(method="POST")
    def generate(self, request: GenerateRequest) -> GenerateResponse:
        import torch

        budget_line = (
            f"\n\nIMPORTANT: keep your prose under {request.max_chars} characters. "
            "Do not include a URL — code will append it."
            if request.max_chars
            else ""
        )

        if request.evidence_text:
            user_block = (
                f"Background article (use as ground truth, paraphrase only):\n"
                f"{request.evidence_text.strip()}\n\n"
                f"X post:\n{request.post_text}\n\n"
                f"Write the Community Note.{budget_line}"
            )
        else:
            user_block = (
                f"X post:\n{request.post_text}\n\n"
                f"Write the Community Note.{budget_line}"
            )

        prompt = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{user_block}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)

        im_end_id = self.tokenizer.convert_tokens_to_ids("<|im_end|>")
        eos_ids = [self.tokenizer.eos_token_id]
        if im_end_id is not None and im_end_id != self.tokenizer.unk_token_id:
            eos_ids.append(im_end_id)

        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                do_sample=True,
                top_p=0.95,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=eos_ids,
            )
        completion = self.tokenizer.decode(
            out[0, inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )

        for marker in [
            "<|im_start|>",
            "<|im_end|>",
            "\nWrite the Community Note",
            "\n\nWrite the Community Note",
            "\nX post:",
            "\nBackground article",
        ]:
            idx = completion.find(marker)
            if idx > 0:
                completion = completion[:idx]

        return GenerateResponse(note_text=completion.strip())
