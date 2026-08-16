
import os
import base64
from huggingface_hub import InferenceClient

client = InferenceClient(
    token=os.getenv("HF_TOKEN"),
)


with open("fakturama_order.png", "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")

# promptOCR = f"""
# Extract this sales order into structured JSON.

# The image is the primary source of truth.
# The OCR output below is supplementary and may contain errors.

# OCR:
# {ocr_text}

# Return:
# - order_reference
# - order_date
# - customer
# - billing_address
# - delivery_address
# - payment
# - items
# - totals

# For every item return:
# - sku
# - description
# - quantity
# - unit_price
# - discount_percent
# - vat_rate
# - line_total

# If something cannot be determined reliably from the image,
# return null rather than guessing.
# """

prompt = f"""
Extract this sales order into structured JSON.

The image is the primary source of truth.

Return:
- order_reference
- order_date
- customer
- billing_address
- delivery_address
- payment
- items
- totals

For every item return:
- sku
- description
- quantity
- unit_price
- discount_percent
- vat_rate
- line_total

If something cannot be determined reliably from the image,
return null rather than guessing.
"""



# completion = client.chat_completion(
#     model="Qwen/Qwen2.5-VL-7B-Instruct:preferred",
#     messages=[
#         {
#             "role": "user",
#             "content": [
#                 {
#                     "type": "text",
#                     "text": prompt,
#                 },
#                 {
#                     "type": "image_url",
#                     "image_url": {
#                         "url": f"data:image/png;base64,{image_data}"
#                     },
#                 },
#             ],
#         }
#     ],
# )

# print(completion.choices[0].message.content)

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