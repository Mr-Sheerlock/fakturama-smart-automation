import io
import json
import base64
import os
import re
from huggingface_hub import InferenceClient


VLM_MODEL = "meta-models/Muse-Glimmer-30B:together"
_hf_client = InferenceClient(token=os.getenv("HF_TOKEN"))


def vlm_call(image, prompt_text):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    completion = _hf_client.chat.completions.create(
        model=VLM_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
    )
    raw = completion.choices[0].message.content
    # Strip markdown fences if the model added them despite instructions.
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)

