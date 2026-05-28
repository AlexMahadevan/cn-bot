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

def _download_base_model():
    """Pre-bake the Qwen 2.5 7B weights into the container image so cold-start
    doesn't have to download them from HuggingFace (which takes ~75s and
    blows past Modal's 60s startup_timeout)."""
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
    max_new_tokens: int = 220
    temperature: float = 0.4
    evidence_text: str | None = None  # optional article text the pipeline retrieved
    max_chars: int | None = None        # char budget for the prose (URL is appended later)


class GenerateResponse(BaseModel):
    note_text: str


@app.cls(
    gpu="A10G",
    volumes={"/models": volume},
    scaledown_window=300,  # keep warm for 5 min between calls
    timeout=120,            # request timeout — generation may take ~10s
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

        budget_line = (
            f"\n\nIMPORTANT: write at most {request.max_chars} characters of prose. "
            "Do not include any URL — code will append it after."
            if request.max_chars
            else ""
        )

        if request.evidence_text:
            user_block = (
                f"Background article (DO NOT COPY — use it to know what's true):\n"
                f"{request.evidence_text.strip()}\n\n"
                f"X post:\n{request.post_text}\n\n"
                f"Write a SHORT Community Note correcting the post. Use the "
                f"background to make sure your facts are right. Paraphrase — "
                f"do not quote or copy the background article verbatim. Do not "
                f"write a URL.{budget_line}"
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

        # Qwen 2.5's chat-template end-of-turn token. Stopping on this prevents
        # the model from rolling past its own turn and emitting fresh prompt
        # templates (which it sometimes does at sampling temperatures).
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

        # Belt-and-suspenders: even with eos_token_id set, sampling can
        # occasionally produce a token sequence that looks like the start
        # of a fresh user turn. Truncate at the first such marker.
        for marker in [
            "<|im_start|>",
            "<|im_end|>",
            "\nWrite the Community Note",
            "\n\nWrite the Community Note",
            "\nX post:",
        ]:
            idx = completion.find(marker)
            if idx > 0:  # >0 so we don't truncate to empty if it starts with marker
                completion = completion[:idx]

        return GenerateResponse(note_text=completion.strip())
