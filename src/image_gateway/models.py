"""The real checkers and the watermark on the GPU. Only the container imports this module.

- QwenPromptGuard: Qwen3Guard-Gen (Apache-2.0, multilingual) labels the request Safe,
  Controversial or Unsafe with categories.
- ShieldGemmaImageGuard: ShieldGemma 2 (Gemma terms) gives the probability that an image
  violates each policy: dangerous, sexual, violence.
- TrustMarkWatermarker: Adobe TrustMark (MIT, code and weights), an invisible watermark that
  survives resizing and re-encoding (LibreChat re-encodes every generated image).
"""

import torch
from PIL import Image

from image_gateway.policy import PromptResult, parse_guard_output


class QwenPromptGuard:
    # Framed as a request to the image model, so the guard judges what would be depicted.
    FRAME = "Create an image: {prompt}"

    def __init__(self, model_id: str, device: str):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = (
            AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.bfloat16)
            .to(device)
            .eval()
        )

    @torch.inference_mode()
    def check(self, text: str) -> PromptResult:
        messages = [{"role": "user", "content": self.FRAME.format(prompt=text)}]
        chat = self.tokenizer.apply_chat_template(messages, tokenize=False)
        inputs = self.tokenizer([chat], return_tensors="pt").to(self.device)
        output = self.model.generate(**inputs, max_new_tokens=32, do_sample=False)
        answer = self.tokenizer.decode(
            output[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
        )
        return parse_guard_output(answer)


class ShieldGemmaImageGuard:
    def __init__(self, model_id: str, device: str):
        from transformers import AutoProcessor, ShieldGemma2ForImageClassification

        self.device = device
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = (
            ShieldGemma2ForImageClassification.from_pretrained(model_id, torch_dtype=torch.bfloat16)
            .to(device)
            .eval()
        )
        # One probability row per policy, in the processor's policy order.
        self.policies = list(self.processor.policy_definitions)

    @torch.inference_mode()
    def scores(self, image: Image.Image) -> dict[str, float]:
        inputs = self.processor(images=[image.convert("RGB")], return_tensors="pt").to(self.device)
        inputs["pixel_values"] = inputs["pixel_values"].to(self.model.dtype)
        probabilities = self.model(**inputs).probabilities  # rows: policies, columns: Yes, No
        return {name: float(probabilities[i][0]) for i, name in enumerate(self.policies)}


class TrustMarkWatermarker:
    def __init__(self, payload: str, model_type: str, strength: float, device: str):
        from trustmark import TrustMark

        self.payload = payload
        self.strength = strength
        self.tm = TrustMark(
            verbose=False,
            model_type=model_type,
            encoding_type=TrustMark.Encoding.BCH_5,
            device=device,
        )

    def apply(self, image: Image.Image) -> Image.Image:
        alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
        marked = self.tm.encode(
            image.convert("RGB"), self.payload, MODE="text", WM_STRENGTH=self.strength
        )
        if alpha is not None:
            marked.putalpha(alpha)
        return marked

    def detect(self, image: Image.Image) -> tuple[str | None, bool]:
        secret, present, _schema = self.tm.decode(image.convert("RGB"), MODE="text")
        return (secret if present else None), bool(present)
