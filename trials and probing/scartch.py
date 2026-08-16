
import os
import base64
from huggingface_hub import InferenceClient

client = InferenceClient(
    token=os.getenv("HF_TOKEN"),
)


with open("fakturamaorder.png", "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")


completion = client.chat.completions.create(
    model="meta-models/Muse-Glimmer-30B:together",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_data}"
                    }
                }
            ]
        }
    ],
)

print(completion.choices[0].message) 