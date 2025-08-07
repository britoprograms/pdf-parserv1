import os
import json
import re 
import fitz  # PyMuPDF
import pytesseract
from pdf2image import convert_from_path
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_ollama import OllamaLLM
from langchain_core.prompts import PromptTemplate
from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 🟢 Create storage folder
UPLOAD_DIR = "PDF_storage"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/PDF_storage", StaticFiles(directory=UPLOAD_DIR), name="PDF_storage")
# 🟢 Database setup
Base = declarative_base()
engine = create_engine("sqlite:///warehouse.db")
Session = sessionmaker(bind=engine)

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True)
    po_number = Column(String, unique=True, nullable=False)
    pdf_path = Column(String, nullable=False)

Base.metadata.create_all(engine)

approved_stores = {"829", "899", "436", "499", "407", "115", "712"}

# 🛠 FIX Prompt: Remove unnecessary variables and escape curly braces properly
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

# 🛠 FIXED Text Extraction Function
def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    fitz_text = "\n".join(page.get_text() for page in doc)
    print("🟡 PyMuPDF extracted:", len(fitz_text.strip()), "characters")
    if len(fitz_text.strip()) > 100:
        return fitz_text
    print("⚠️ Falling back to OCR...")
    images = convert_from_path(pdf_path)
    ocr_text = "\n".join(pytesseract.image_to_string(img) for img in images)
    print("🟡 OCR extracted:", len(ocr_text.strip()), "characters")
    return ocr_text

def clean_text(text):
    text = text.lower()
    text = re.sub(r'[\n\r]+', ' ', text)
    text = re.sub(r'[^a-z0-9#:\-. ]', '', text)
    return re.sub(r' +', ' ', text).strip()

@app.post("/upload/", response_model=None)
async def upload_pdf(file: UploadFile = File(...)):
    file_location = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_location, "wb") as f:
        f.write(await file.read())

    raw_text = extract_text_from_pdf(file_location)
    cleaned_text = clean_text(raw_text)

    if not cleaned_text.strip():
        print("❌ No text found.")
        return JSONResponse(status_code=400, content={"error": "No text extracted."})

    print("📄 Text Preview:", cleaned_text[:300])

    # 🛠 FIXED LLM CALL
    result = translator_chain.invoke({"raw_text": cleaned_text})
    print("🧠 Raw LLM Result:", result)

    # 🛠 FIXED JSON PARSE with regex fallback
    json_match = re.search(r'\{.*?\}', result, re.DOTALL)
    if json_match:
        try:
            po_data = json.loads(json_match.group())
        except json.JSONDecodeError:
            print("❌ Invalid JSON structure:", json_match.group())
            return JSONResponse(status_code=400, content={"error": "Bad JSON", "raw": result})
    else:
        print("❌ No JSON in response.")
        return JSONResponse(status_code=400, content={"error": "No JSON", "raw": result})

    translated_po = po_data.get("translated_po", "UNKNOWN")
    store_code = translated_po.split("-")[0] if "-" in translated_po else "UNKNOWN"
    if store_code not in approved_stores:
        print("❌ Invalid store code:", store_code)
        translated_po = "UNKNOWN"

    session = Session()
    po_entry = PurchaseOrder(po_number=translated_po, pdf_path=file_location)
    session.add(po_entry)
    session.commit()

    return JSONResponse(content={"po_number": translated_po, "pdf_path": file_location})

# --- Search Route ---
@app.get("/search/{po}", response_model=None)
async def search_po(po: str):
    session = Session()
    entry = session.query(PurchaseOrder).filter_by(po_number=po).first()
    if entry:
        return JSONResponse(content={"pdf_link": f"/PDF_storage/{os.path.basename(entry.pdf_path)}"})
    return JSONResponse(status_code=404, content={"error": "PO not found"})

# --- Frontend HTML UI ---
@app.get("/")
async def custom_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

