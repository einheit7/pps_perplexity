from flask import Flask, request, render_template, send_file, redirect, flash
import pandas as pd
import json
import os
import re
from io import BytesIO
from openpyxl import Workbook
import requests
import logging
from urllib.parse import quote  # URL 인코딩

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Flash 메시지용. 실제 배포 시에는 랜덤한 값으로 변경!

# 로깅 설정
logging.basicConfig(level=logging.INFO)

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
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "your_api_key_here") # 환경 변수
API_BASE_URL = "https://api.perplexity.ai"

def search_price_api(product_name, system_prompt, model="sonar"):
    headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}"}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Find the highest and lowest prices for '{product_name}' in Korean Won (KRW), including VAT."},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
    }

    try:
        # 실제 API 호출
        response = requests.post(
            f"{API_BASE_URL}/chat/completions",  # 엔드포인트
            json=payload,
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        content_str = data["choices"][0]["message"]["content"]

        content_str = clean_json_response(content_str) # Perplexity 모델에 따라 필요 여부 결정
        try:
            content_json = json.loads(content_str)
            content_json["highest_price"] = process_price(content_json.get("highest_price"))
            content_json["lowest_price"] = process_price(content_json.get("lowest_price"))
            return content_json
        except json.JSONDecodeError as e:
            app.logger.error(f"JSONDecodeError: {e}, Response: {content_str}")
            return _create_empty_result() # 빈 결과 반환 함수 사용

    except requests.exceptions.RequestException as e:
        app.logger.error(f"API 호출 오류: {e}")
        return _create_empty_result()

    except Exception as e:
        app.logger.error(f"기타 오류: {e}")
        return _create_empty_result()

def _create_empty_result():
    """빈 결과 딕셔너리를 반환"""
    return {
        "highest_price": None, "highest_price_product": None, "highest_price_source": None,
        "highest_price_url": None,
        "lowest_price": None, "lowest_price_product": None, "lowest_price_source": None,
        "lowest_price_url": None
    }

# --- 기본 System Prompt ---
def get_default_system_prompt():
    return (
        "**BEGIN JSON ONLY INSTRUCTION**\n"
        "You are a helpful assistant that provides price information.  "
        "Your response MUST be valid JSON and nothing else.  Do NOT include any conversational text, only JSON. "
        "Return results in the following JSON structure, filling in the values.  "
        "Prices MUST be in Korean Won (KRW), including VAT (Value Added Tax).  "
        "If a price is not available or if it's unclear whether VAT is included, use `null`. "
        "If possible, include commas as thousands separators (e.g., '1,947,000원'). "
        "Search for prices on Korean online shopping sites. "
        "If a value cannot be found, fill it with `null`. Ensure all fields, including numeric price fields, are properly formatted according to JSON syntax (e.g., with commas separating key-value pairs).\n\n"
        "```json\n"
        "{\n"
        '  "highest_price": 1299,\n'
        '  "highest_price_product": "Example Product",\n'
        '  "highest_price_source": "Example Source",\n'
        '  "highest_price_url": "https://www.example.com",\n'
        '  "lowest_price": 999,\n'
        '  "lowest_price_product": "Example Product 2",\n'
        '  "lowest_price_source": "Example Source 2",\n'
        '  "lowest_price_url": "https://www.example.com/2",\n'  # 수정된 부분
        "}\n"
        "```\n"
        "**END JSON ONLY INSTRUCTION**"
    )

# --- 메인 라우트 ---
@app.route("/", methods=["GET", "POST"])
def index():
    default_system_prompt = get_default_system_prompt()  # 함수 호출로 변경

    if request.method == "POST":
        # ... (파일 업로드, 엑셀 처리 등 이전 코드와 동일) ...
        if "excel_file" not in request.files:
            flash("파일이 업로드되지 않았습니다.")
            return redirect(request.url)

        file = request.files["excel_file"]
        if file.filename == "":
            flash("선택된 파일이 없습니다.")
            return redirect(request.url)

        output_filename = request.form.get("output_filename", "price_results.xlsx")
        if not output_filename.endswith((".xlsx", ".xls")):
            output_filename += ".xlsx"

        system_prompt = request.form.get("system_prompt", default_system_prompt)
        model = request.form.get("model", "sonar")

        try:
            df = pd.read_excel(file)
            products = df.iloc[:, 0].tolist()
        except Exception as e:
            flash(f"엑셀 파일 읽기 오류: {e}")
            return redirect(request.url)

        results = []
        total_products = len(products)
        for i, product in enumerate(products):
            app.logger.info(f"[{i+1}/{total_products}] 처리 중: {product}")
            price_data = search_price_api(product, system_prompt, model)
            price_data["product_name"] = product
            results.append(price_data)

        # Excel 파일 메모리 내 생성
        output = BytesIO()
        wb = Workbook()
        ws = wb.active
        headers = [
            "상품명", "highest_price", "highest_price_product", "highest_price_source",
            "highest_price_url", "lowest_price", "lowest_price_product", "lowest_price_source", "lowest_price_url"
        ]
        ws.append(headers)
        for res in results:
            ws.append([
                res.get("product_name"), res.get("highest_price"), res.get("highest_price_product"),
                res.get("highest_price_source"), res.get("highest_price_url"), res.get("lowest_price"),
                res.get("lowest_price_product"), res.get("lowest_price_source"), res.get("lowest_price_url")
            ])
        wb.save(output)
        output.seek(0)

        return send_file(output, download_name=output_filename, as_attachment=True)

    return render_template("index.html", default_system_prompt=default_system_prompt)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
