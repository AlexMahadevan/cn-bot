"""Inference endpoint for the fine-tuned CN-bot model on Modal.

Loads Qwen 2.5 7B + the LoRA adapter trained by finetune_modal.py, exposes
an HTTPS POST endpoint that the bot calls in place of Anthropic Opus.

Deploy:
    modal deploy scripts/serve_finetune_modal.py

That prints a stable URL like:
    https://YOURNAME--cn-bot-inference-generate.modal.run

Set CN_BOT_FINETUNED_URL in the bot's .env to that URL and the bot will
route note generation through it instead of through Opus.

Cost: Modal A10G at $0.000604/sec, ~3-5 sec per inference = ~$0.002 per
note. Vs. Opus 4.7 at ~$0.05 per note. ~25x cheaper.
"""

from __future__ import annotations

import modal
from pydantic import BaseModel

APP_NAME = "cn-bot-inference"
VOLUME_NAME = "cn-bot-models"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
ADAPTER_PATH = "/models/cn-bot-qwen25-7b-lora"

SYSTEM_PROMPT = (
    "You write Community Notes for X. Given a post, produce a helpful note "
    "in the style raters approve: direct factual correction, named source, "
    "tight prose, ends with a citation URL. Match what an X reader would "
    "rate Currently Rated Helpful."
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
)

app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name(VOLUME_NAME)


class GenerateRequest(BaseModel):
    post_text: str
    max_new_tokens: int = 220
    temperature: float = 0.4


class GenerateResponse(BaseModel):
    note_text: str


@app.cls(
    gpu="A10G",
    volumes={"/models": volume},
    scaledown_window=300,  # keep warm for 5 min between calls
    timeout=60,
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
        print(f"loaded {BASE_MODEL} + adapter at {ADAPTER_PATH}")

    @modal.fastapi_endpoint(method="POST")
    def generate(self, request: GenerateRequest) -> GenerateResponse:
        import torch

        prompt = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\nX post:\n{request.post_text}\n\nWrite the Community Note.<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                do_sample=True,
                top_p=0.95,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        completion = self.tokenizer.decode(
            out[0, inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )
        return GenerateResponse(note_text=completion.strip())
