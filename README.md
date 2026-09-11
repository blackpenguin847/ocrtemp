# estimate-ocr

스캔된 견적 원가명세서 PDF를 **로컬 Ollama vision 모델**로 읽어 구조화된 데이터(JSON / CSV / XLSX)로 변환하는 CLI 도구입니다. 외부 API 호출 없이 전부 로컬에서 처리합니다.

## 요구 사항

- Python 3.10+
- [Ollama](https://ollama.com) (로컬 실행 중)
- VRAM 8GB 이상 권장

## 설치

```bash
# 1) Ollama 모델 준비 (VRAM 8GB 기준)
ollama pull qwen3-vl:8b

# 2) 패키지 설치
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
```

### 모델 선택

| 모델 | 크기 | 비고 |
|---|---|---|
| `qwen3-vl:8b` | ~6GB | **기본값.** 한국어/CJK OCR에 가장 강함 |
| `minicpm-v` | ~5.5GB | 문서 OCR 특화, 더 가벼움. 비교용으로 유용 |
| `llama3.2-vision:11b` | ~8GB | 8GB VRAM에서는 빠듯함. `--max-edge 1280` 권장 |

여러 모델로 같은 PDF를 돌려 결과를 비교한 뒤 고르는 것을 권합니다.

## 사용법

```bash
# 기본: 폴더 안의 모든 PDF → out/result.xlsx
estimate-ocr ./samples -o out

# 단일 파일, 모든 형식으로 출력
estimate-ocr 견적서_2024.pdf -f all

# 특정 페이지만
estimate-ocr 견적서.pdf --pages 1,3,5-7

# 다른 모델 사용
estimate-ocr ./samples -m minicpm-v

# VRAM 부족(OOM)이거나 너무 느릴 때
estimate-ocr ./samples --max-edge 1280 --num-ctx 4096

# 인식 정확도만 먼저 확인 (구조화 없이 원문 텍스트만)
estimate-ocr 견적서.pdf --raw-text -o out

# 전처리된 이미지 확인 (out/_debug/ 에 저장)
estimate-ocr 견적서.pdf --debug-images -v
```

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `-o, --output` | `out` | 결과 저장 폴더 |
| `-f, --format` | `xlsx` | `json` / `csv` / `xlsx` / `all` |
| `-m, --model` | `qwen3-vl:8b` | Ollama 모델명 |
| `--host` | `http://localhost:11434` | Ollama 서버 주소 |
| `--dpi` | `200` | PDF 렌더링 해상도 |
| `--max-edge` | `1600` | 이미지 최대 변 길이(px). VRAM 부족 시 낮출 것 |
| `--num-ctx` | `8192` | 모델 컨텍스트 길이 |
| `--timeout` | `600` | 응답 타임아웃(초) |
| `--pages` | 전체 | `1,3,5-7` 형식 |
| `--raw-text` | off | 구조화 없이 원문만 추출 |

## 출력 결과

`out/result.xlsx`

- **추출결과 시트**: 한 행 = 명세서의 한 품목. 원본 PDF명, 페이지, 문서번호, 품명, 규격, 단위, 수량, 단가, 금액, 비고
- **검수 시트**: 자동 검증에서 걸린 경고 목록

자동 검증은 다음을 확인합니다.

- `수량 × 단가 ≠ 금액` 인 행
- 품명이 비어 있는 행
- 품목 금액 합계와 문서상 소계의 불일치
- JSON 파싱 실패 / 품목 0건

문제가 있는 행은 xlsx에서 **노란색으로 표시**되므로, 해당 행만 원본 스캔과 대조하면 됩니다.

## 정확도를 높이는 방법

1. **먼저 `--raw-text`로 확인.** 원문 텍스트조차 제대로 안 읽히면 프롬프트가 아니라 이미지 품질 문제입니다. `--dpi 300`으로 올려보세요.
2. **`--debug-images`로 전처리 결과 확인.** 스캔이 기울어졌거나 너무 어두우면 인식률이 크게 떨어집니다.
3. **양식이 조금씩 다른 경우**, `src/estimate_ocr/prompts.py`의 `USER_PROMPT` 스키마에 실제 문서에서 자주 쓰이는 열 이름을 추가하세요. `models.py`의 `pick()` 키 목록에도 함께 추가하면 매핑됩니다.
4. 로컬 vision 모델은 클라우드 OCR보다 숫자 오인식이 잦습니다. **검수 시트를 반드시 확인하는 워크플로**를 전제로 사용하세요.

## 구조

```
src/estimate_ocr/
├─ cli.py            # CLI 진입점, 인자 파싱
├─ extractor.py      # 파이프라인 오케스트레이션 + fallback 재시도
├─ ollama_client.py  # Ollama HTTP 클라이언트, JSON 관대 파싱
├─ pdf_render.py     # PDF → 이미지 (그레이스케일/대비보정/리사이즈)
├─ prompts.py        # 프롬프트 템플릿
├─ models.py         # 데이터 모델 + 자동 검증 로직
└─ exporter.py       # JSON / CSV / XLSX 출력
```

라이브러리로도 사용 가능합니다.

```python
from estimate_ocr import OllamaClient, Extractor

client = OllamaClient(model="qwen3-vl:8b")
docs = Extractor(client).extract_pdf("견적서.pdf")

for doc in docs:
    print(doc.page, len(doc.items), doc.validate())
```

## 개발

```bash
pip install -e ".[dev]"
pytest
```

테스트는 Ollama 없이 동작합니다. 가짜 클라이언트로 렌더링 → 추출 → fallback 재시도 → 출력까지의 경로를 검증합니다.

## 라이선스

MIT
