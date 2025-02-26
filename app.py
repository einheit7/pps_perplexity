from flask import Flask, request, render_template, send_file, redirect, flash
import pandas as pd
import json
import os
import re
from io import BytesIO
from openpyxl import Workbook
import requests

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Flash 메시지용

# --- 유틸리티 함수 ---
def clean_json_response(response_str):
    response_str = response_str.strip()
    if response_str.startswith("```"):
        lines = response_str.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        response_str = "\n".join(lines).strip()
    return response_str

def process_price(price_str):
    if price_str is None:
        return None
    if isinstance(price_str, (int, float)):
        return price_str
    price_str = re.sub(r"[^0-9,]", "", price_str)
    try:
        price_int = int(price_str.replace(",", ""))
        if "VAT 별도" in price_str or "(별도)" in price_str:
            price_int = int(price_int * 1.1)
        return price_int
    except ValueError:
        return None

# --- API 호출 함수 ---
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "your_api_key_here")
API_BASE_URL = "https://api.perplexity.ai"  # 실제 API 엔드포인트로 변경

def search_price_api(product_name, system_prompt, model="sonar"):
    headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}"}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Find the highest and lowest prices for '{product_name}' in Korean Won (KRW), including VAT."}
    ]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7
    }
    # 실제 API 호출 (예시: requests.post 사용)
    # response = requests.post(f"{API_BASE_URL}/v1/chat/completions", json=payload, headers=headers)
    # data = response.json()
    # content_str = data["choices"][0]["message"]["content"]

    # 데모용 더미 응답:
    dummy_response = '''
    ```json
    {
      "highest_price": "1,336,800원",
      "highest_price_product": "33498 디지털테스터기 FLUKE-87-5 1 000V (W678924)",
      "highest_price_source": "G마켓",
      "highest_price_url": "https://m.gmarket.co.kr/n/search?keyword=fluke",
      "lowest_price": "716,300원",
      "lowest_price_product": "플루크 디지털 테스터기 FLUKE-87-5 멀티미터",
      "lowest_price_source": "G마켓",
      "lowest_price_url": "https://m.gmarket.co.kr/n/search?keyword=fluke"
    }
    ```'''
    content_str = clean_json_response(dummy_response)
    try:
        content_json = json.loads(content_str)
        content_json["highest_price"] = process_price(content_json.get("highest_price"))
        content_json["lowest_price"] = process_price(content_json.get("lowest_price"))
    except json.JSONDecodeError:
        content_json = {
            "highest_price": None,
            "highest_price_product": None,
            "highest_price_source": None,
            "highest_price_url": None,
            "lowest_price": None,
            "lowest_price_product": None,
            "lowest_price_source": None,
            "lowest_price_url": None,
        }
    return content_json

# --- 메인 라우트 ---
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if "excel_file" not in request.files:
            flash("파일이 업로드되지 않았습니다.")
            return redirect(request.url)
        file = request.files["excel_file"]
        if file.filename == "":
            flash("선택된 파일이 없습니다.")
            return redirect(request.url)
        try:
            df = pd.read_excel(file)
            products = df.iloc[:, 0].tolist()
        except Exception as e:
            flash("엑셀 파일을 읽는 중 오류가 발생했습니다.")
            return redirect(request.url)
        
        output_filename = request.form.get("output_filename", "price_results.xlsx")
        system_prompt = request.form.get("system_prompt", "")
        model = request.form.get("model", "sonar")
        
        results = []
        for product in products:
            price_data = search_price_api(product, system_prompt, model)
            price_data["product_name"] = product
            results.append(price_data)
        
        # Excel 파일 메모리 내 생성
        output = BytesIO()
        wb = Workbook()
        ws = wb.active
        headers = [
            "상품명",
            "highest_price",
            "highest_price_product",
            "highest_price_source",
            "highest_price_url",
            "lowest_price",
            "lowest_price_product",
            "lowest_price_source",
            "lowest_price_url",
        ]
        ws.append(headers)
        for res in results:
            ws.append([
                res.get("product_name"),
                res.get("highest_price"),
                res.get("highest_price_product"),
                res.get("highest_price_source"),
                res.get("highest_price_url"),
                res.get("lowest_price"),
                res.get("lowest_price_product"),
                res.get("lowest_price_source"),
                res.get("lowest_price_url"),
            ])
        wb.save(output)
        output.seek(0)
        
        return send_file(output, attachment_filename=output_filename, as_attachment=True)
    
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
