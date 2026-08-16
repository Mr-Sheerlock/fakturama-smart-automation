import pytesseract
from PIL import Image

# text = pytesseract.image_to_data(
#     Image.open("fakturamaorder.png"),
    
# )

text = pytesseract.image_to_string(
    Image.open("fakturamaorder.png"),
    
)

print(text)