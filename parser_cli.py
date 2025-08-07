import sys
import json
import re
import fitz  # PyMuPDF
import pytesseract
from pdf2image import convert_from_path
from langchain_ollama import OllamaLLM
from langchain_core.prompts import PromptTemplate

# Approved stores
approved_stores = {"829", "899", "436", "499", "407", "115", "712"}

# Prompt template
FEW_SHOT_PROMPT = """
You are a warehouse AI assistant. You receive messy text from vendor purchase orders and your job is to extract the PO number and store number, and normalize those into our internal PO number system.

ONLY return JSON. Do not explain or ask for input. Just respond with JSON.

🎯 Goal:
Return a single field — "translated_po" — in the format: "XXX-YYYYY"
- XXX = store number from this list: 829, 899, 436, 499, 407, 115, 712
- YYYYY = 5-digit PO number

Rules:
1. PO may have extra characters or be inside phrases like PO#: B00911-AZ
2. Extract clean 5-digit PO number and approved store number from text.
3. If no valid store found, respond with:
{{"translated_po": "UNKNOWN"}}
4. ❌ Do NOT infer store numbers based on PO numbers, locations, or patterns.
❌ If no valid store number (exact match) is found in the input text, you MUST return:
   {{"translated_po": "UNKNOWN"}}

Examples:

Input:
"PO# 10432, Destination: 999"
→ 999 is not an approved store number.
Output:
{{"translated_po": "UNKNOWN"}}

Input:
"Ship to Store: 436 — PO: 10432"
Output:
{{"translated_po": "436-10432"}}

---

Input:
"ORDER NO. 994219. BRANCH 407"
→ Correct PO = 94219 (ignore the extra 9)
Output:
{{"translated_po": "407-94219"}}

---

Input:
"PO# B00911-AZ, Ship to: 115"
→ Extract '00911' as valid PO number
Output:
{{"translated_po": "115-00911"}}

---

Input:
"Distribution Center 712. Ref: PO-V89920091-FTL"
→ PO = 20091 (not 89920 or 89920091)
Output:
{{"translated_po": "712-20091"}}

---
 

Input:
{raw_text}
Output:
"""

prompt = PromptTemplate(input_variables=["raw_text"], template=FEW_SHOT_PROMPT)
llm = OllamaLLM(model="llama3")
translator_chain = prompt | llm

def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    fitz_text = "\n".join(page.get_text() for page in doc)
    if len(fitz_text.strip()) > 100:
        return fitz_text
    images = convert_from_path(pdf_path)
    ocr_text = "\n".join(pytesseract.image_to_string(img) for img in images)
    return ocr_text

def clean_text(text):
    text = text.lower()
    text = re.sub(r'[\n\r]+', ' ', text)
    text = re.sub(r'[^a-z0-9#:\-. ]', '', text)
    return re.sub(r' +', ' ', text).strip()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No file path provided"}))
        sys.exit(1)

    file_path = sys.argv[1]
    raw_text = extract_text_from_pdf(file_path)
    cleaned_text = clean_text(raw_text)

    if not cleaned_text.strip():
        print(json.dumps({"error": "No text extracted"}))
        sys.exit(1)

    result = translator_chain.invoke({"raw_text": cleaned_text})
    json_match = re.search(r'\{.*?\}', result, re.DOTALL)
    if json_match:
        try:
            po_data = json.loads(json_match.group())
        except json.JSONDecodeError:
            print(json.dumps({"error": "Bad JSON", "raw": result}))
            sys.exit(1)
    else:
        print(json.dumps({"error": "No JSON", "raw": result}))
        sys.exit(1)

    translated_po = po_data.get("translated_po", "UNKNOWN")
    store_code = translated_po.split("-")[0] if "-" in translated_po else "UNKNOWN"
    if store_code not in approved_stores:
        translated_po = "UNKNOWN"

    print(json.dumps({"po_number": translated_po}))

