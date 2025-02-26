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
from queue import Queue, Empty  # Empty 추가

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Flash 메시지용

# --- 로깅 설정 ---
log_queue = Queue()

class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

handler = QueueHandler(log_queue)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

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
    if price_str is None: return None
    if isinstance(price_str, (int, float)): return price_str
    price_str = re.sub(r"[^0-9,]", "", price_str)
    try:
        price_int = int(price_str.replace(",", ""))
        if "VAT 별도" in price_str.lower() or "(별도)" in price_str.lower():
            price_int = int(price_int * 1.1)
        return price_int
    except ValueError:
        return None

# --- API 호출 함수 ---
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "your_api_key_here")
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
        response = requests.post(
            f"{API_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        content_str = data["choices"][0]["message"]["content"]

        content_str = clean_json_response(content_str)  # 주석 해제/처리. Perplexity 응답 형식에 따라.
        try:
            content_json = json.loads(content_str)
            content_json["highest_price"] = process_price(content_json.get("highest_price"))
            content_json["lowest_price"] = process_price(content_json.get("lowest_price"))
            return content_json
        except json.JSONDecodeError as e:
            root_logger.error(f"JSONDecodeError: {e}, Response: {content_str}")
            return _create_empty_result()

    except requests.exceptions.RequestException as e:
        root_logger.error(f"API 호출 오류: {e}")
        return _create_empty_result()
    except Exception as e:
        root_logger.error(f"기타 오류: {e}")
        return _create_empty_result()

def _create_empty_result():
    return { "highest_price": None, "highest_price_product": None, "highest_price_source": None,
            "highest_price_url": None, "lowest_price": None, "lowest_price_product": None,
            "lowest_price_source": None, "lowest_price_url": None }

# --- 백그라운드 작업 (가격 검색) ---
def background_search(file_path, output_filename, system_prompt, model):
    try:
        df = pd.read_excel(file_path)
        products = df.iloc[:, 0].tolist()

        results = []
        total_products = len(products)
        for i, product in enumerate(products):
            root_logger.info(f"[{i+1}/{total_products}] {product} 가격 검색 시작...")  # API 호출 전 로그
            price_data = search_price_api(product, system_prompt, model)
            root_logger.info(f"[{i+1}/{total_products}] {product} 가격 검색 완료.")  # API 호출 후 로그
            price_data["product_name"] = product
            results.append(price_data)

        # Excel 파일 생성 (이전 코드와 동일)
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

        root_logger.info("가격 검색 완료.")

    except Exception as e:
        root_logger.error(f"가격 검색 중 오류 발생: {e}")
    finally:
        # 임시 파일 삭제
        try:
            os.remove(file_path)
        except Exception as e:
            root_logger.error(f"임시 파일 삭제 오류: {e}")

# --- Flask 라우트 ---
result_file = None

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
            try:
                message = log_queue.get(timeout=1)  # 큐에서 메시지 가져오기 (1초 타임아웃)
                yield f"data: {message}\n\n"
            except Empty:  # 큐가 비어있는 경우
                yield f"data: \n\n" # 빈 data event를 보내서 연결 유지
            # time.sleep(0.5) # 짧게 변경

    return Response(generate(), mimetype='text/event-stream')

@app.route("/download")
def download_file():
    global result_file
    if result_file:
        response =  send_file(result_file, download_name="price_results.xlsx", as_attachment=True)
        result_file = None
        return response

    else:
        return "No result file available", 404

@app.route('/upload_prompt', methods=['POST'])
def upload_prompt():
    # ... (이전 코드와 동일) ...
    if 'prompt_file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['prompt_file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file:
        try:
            prompt_content = file.read().decode('utf-8')  # 파일 내용 읽기
            return jsonify({'message': 'Prompt loaded successfully', 'prompt': prompt_content})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
@app.route('/download_prompt', methods=['GET'])
def download_prompt():
    # ... (이전 코드와 동일) ...
    try:
        prompt_content = request.args.get('prompt_content')
        prompt_bytes = prompt_content.encode('utf-8')
        return send_file(
            BytesIO(prompt_bytes),
            as_attachment=True,
            download_name="prompt.txt",
            mimetype="text/plain"
        )
    except Exception as e:
        return str(e), 500

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
