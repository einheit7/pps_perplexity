from flask import Flask, request, render_template, send_file, redirect, flash, jsonify, Response
import pandas as pd
import json
import os
import re
from io import BytesIO
from openpyxl import Workbook
import requests
import logging
import time
import threading
from queue import Queue

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Flash 메시지용

# --- 로깅 설정 ---
log_queue = Queue()  # 로그 메시지를 저장할 큐

class QueueHandler(logging.Handler):  # 큐를 사용하는 로깅 핸들러
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

# 기본 로거 설정
handler = QueueHandler(log_queue)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)  # root logger의 레벨을 INFO로

# --- 유틸리티 함수 ---
def clean_json_response(response_str):
    # ... (이전 코드와 동일) ...
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
   # ... (이전 코드와 동일) ...
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
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "your_api_key_here") # 환경 변수에서 API키
API_BASE_URL = "https://api.perplexity.ai"

def search_price_api(product_name, system_prompt, model="sonar"):
    # ... (이전 코드와 동일) ...
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
        # 실제 API 호출 (requests 사용)
        response = requests.post(
            f"{API_BASE_URL}/chat/completions",  # 실제 엔드포인트로 변경
            json=payload,
            headers=headers,
            timeout=15  # 적절한 타임아웃 설정
        )
        response.raise_for_status()  # 200 OK가 아니면 예외 발생
        data = response.json()
        content_str = data["choices"][0]["message"]["content"]

        content_str = clean_json_response(content_str)  # Perplexity 모델에 따라 필요 여부 결정
        try:
            content_json = json.loads(content_str)
            content_json["highest_price"] = process_price(content_json.get("highest_price"))
            content_json["lowest_price"] = process_price(content_json.get("lowest_price"))
            return content_json
        except json.JSONDecodeError as e:
             app.logger.error(f"JSONDecodeError: {e}, Response: {content_str}") # 로그 기록
             return _create_empty_result() # 빈 결과 반환


    except requests.exceptions.RequestException as e:
        app.logger.error(f"API 호출 오류: {e}") #  로그
        return _create_empty_result()

    except Exception as e: # 기타 예외처리
        app.logger.error(f"기타 오류: {e}")
        return _create_empty_result()
def _create_empty_result():
    return {
        "highest_price": None,
        "highest_price_product": None,
        "highest_price_source": None,
        "highest_price_url": None,
        "lowest_price": None,
        "lowest_price_product": None,
        "lowest_price_source": None,
        "lowest_price_url": None,
    }

# --- 백그라운드 작업 (가격 검색) ---
def background_search(file_path, output_filename, system_prompt, model):
    try:
        df = pd.read_excel(file_path)
        products = df.iloc[:, 0].tolist()

        results = []
        total_products = len(products)
        for i, product in enumerate(products):
            logging.info(f"[{i + 1}/{total_products}] {product} 가격 검색 중...")  # 로그 메시지
            price_data = search_price_api(product, system_prompt, model)
            price_data["product_name"] = product
            results.append(price_data)
            # time.sleep(1) # 짧은 지연시간

        # Excel 파일 생성
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

        # 결과 파일 저장 (global 변수 사용)
        global result_file
        result_file = output

        logging.info("가격 검색 완료.")


    except Exception as e:
        logging.error(f"가격 검색 중 오류 발생: {e}")
    finally:
        # 임시 파일 삭제
        os.remove(file_path)

# --- Flask 라우트 ---
result_file = None  # 결과 파일을 저장할 전역 변수

@app.route("/", methods=["GET"])
def index():
    default_system_prompt = get_default_system_prompt()
    return render_template("index.html", default_system_prompt=default_system_prompt)

@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"message": "파일이 없습니다."}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"message": "파일이 선택되지 않았습니다."}), 400
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"message": "엑셀 파일만 업로드 가능합니다."}), 400

    # 파일 저장 (임시 파일)
    file_path = os.path.join("./tmp", file.filename)
    os.makedirs("./tmp", exist_ok=True)  # tmp 폴더 생성
    file.save(file_path)
    return jsonify({"message": "파일 업로드 성공", "file_path": file_path})

@app.route("/search", methods=["POST"])
def start_search():
    file_path = request.form.get("file_path")
    output_filename = request.form.get("output_filename", "price_results.xlsx")
    if not output_filename.endswith((".xlsx", ".xls")):
        output_filename += ".xlsx"
    system_prompt = request.form.get("system_prompt", get_default_system_prompt())
    model = request.form.get("model", "sonar")

    if not file_path:
        return jsonify({"message": "파일 경로가 없습니다."}), 400

    # 백그라운드 스레드에서 가격 검색 실행
    thread = threading.Thread(target=background_search, args=(file_path, output_filename, system_prompt, model))
    thread.start()

    return jsonify({"message": "가격 검색 시작됨"})

@app.route("/logs")
def stream_logs():
    def generate():
        while True:
            # 큐에서 로그 메시지 가져오기
            message = log_queue.get()
            yield f"data: {message}\n\n"  # SSE 형식
            time.sleep(0.5) # 폴링 간격

    return Response(generate(), mimetype='text/event-stream')

@app.route("/download")
def download_file():
    global result_file
    if result_file:
        response =  send_file(result_file, download_name="price_results.xlsx", as_attachment=True)
        result_file = None # 전역변수 초기화
        return response

    else:
        return "No result file available", 404

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
        '  "lowest_price_url": "https://www.example.com/2",\n'
        "}\n"
        "```\n"
        "**END JSON ONLY INSTRUCTION**"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
